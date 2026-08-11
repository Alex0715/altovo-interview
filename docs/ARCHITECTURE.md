# Architecture & Design Note

**Project:** Grounded document Q&A — Altovo take-home
**Status:** written before the build, updated only where reality disagreed
**Budget:** 6–8 hours

---

## 1. How I broke the problem down

The brief is "upload documents, ask questions, get grounded answers with sources." Stripped down, that is four jobs:

1. **Ingest** — get bytes into clean text with structure I can point back at later.
2. **Retrieve** — given a question, find the passages most likely to contain the answer.
3. **Generate** — answer *only* from those passages, and say so when they don't suffice.
4. **Attribute** — show the user exactly which passage produced which claim, in a way they can verify in seconds.

Most RAG demos treat (4) as a footnote — a list of filenames under the answer. I think it is the actual product. An answer you can't check is worth less than no answer, so attribution is where I'm spending disproportionate effort, and where I've made the least convenient engineering choices (server-side citation validation, character offsets stored at ingest time).

The corresponding decision is what I am *not* building: this is a single-user, small-corpus, read-only tool. Every choice below optimises for that and would be revisited at scale. Section 9 lists what changes.

---

## 2. Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js (App Router), TypeScript | Required. App Router route handlers also serve as the API proxy. |
| Backend | Python, FastAPI | Required. Async, native SSE streaming, Pydantic gives me request/response contracts for free. |
| Database | Neon Postgres + `pgvector` | Vectors, full-text index, and application state in **one** store. No second system to deploy, back up, or keep consistent. |
| Chat model | `lexora.network` (OpenAI-compatible) | Provider specified for this exercise. |
| Embeddings | OpenAI `text-embedding-3-small` (1536d) | The gateway is chat-completions-only, so embeddings go direct. ~$0.02/M tokens — the whole exercise costs cents. |
| Hosting | Vercel (web) + Railway (API) + Neon (DB) | Railway over Render because Render's free tier sleeps and cold-starts in 30–60s; a reviewer opening the link cold shouldn't wait. |

**Both model providers sit behind interfaces** (`LLMClient`, `Embedder`). Two providers with different failure modes is exactly the situation where you don't want vendor calls scattered through business logic — and it means swapping either is a one-file change.

### Provider constraints (verified before building, not assumed)

I probed both providers before writing application code. The gateway is *not* a drop-in OpenAI clone — it validates against a strict parameter whitelist:

| Parameter | Behaviour |
|---|---|
| `temperature`, `max_tokens`, `response_format`, `tools` | accepted |
| `seed`, `top_p`, `stop`, `max_completion_tokens`, `stream_options` | rejected, `400` |
| `stream: true` | **accepted and silently ignored** — returns a complete JSON body |

Two things follow, and they reshaped the design:

- **There is no token streaming.** The `stream` flag is the dangerous kind of unsupported: no error, just a response that never chunks. Discovering this at hour 8 would have meant a rewrite of both the SSE layer and the chat UI. See §6.
- **`response_format: {"type":"json_object"}` works**, which changes the citation design for the better — see §5.

Measured: ~3.2s for a 315-token completion; embeddings ~0.4s warm. No `seed` support means generations aren't bit-reproducible, which is a caveat on the eval set (§10) rather than a blocker, since I run at `temperature: 0`.

### Why Postgres and not a vector DB

For a corpus this size, a dedicated vector store buys nothing and costs a deployment target. pgvector with an HNSW index handles thousands of chunks at single-digit milliseconds. More importantly, keeping chunks and vectors in the same table lets me do hybrid retrieval **in one SQL statement** — the lexical and semantic arms fuse in the database instead of in Python.

---

## 3. Ingestion

**Synchronous, on the upload request.** No queue, no worker, no object storage. A handful of documents parses in a few seconds. A `status` column (`parsing → embedding → ready | failed`) lets the UI show progress, and the client polls it. This is a deliberate deferral, not an oversight — see §9.

Original files are parsed in memory and **never persisted**. Text and chunks go to Postgres; the blob is dropped. This removes S3/blob storage from the system entirely, and means "delete document" is one `DELETE ... CASCADE`.

**Formats:** PDF (`pypdf`), plain text, and Markdown. If a PDF yields no extractable text it is almost certainly scanned, and I fail it with an explicit *"this looks like a scanned document; OCR isn't supported"* rather than silently ingesting an empty document. **Detecting the failure honestly matters more than handling it.**

### Chunking

Fixed-size chunking splits sentences mid-thought and produces citations that point at fragments. Instead:

- Extract text **per page**, keeping the page number.
- Split on paragraph boundaries, then pack paragraphs into chunks of **~700 tokens with ~100 tokens of overlap**. Overlap costs storage and buys robustness when an answer straddles a boundary.
- Never merge across a document boundary; do allow merging across a page boundary (headings and their bodies get separated by pagination constantly).
- Record for every chunk: `page_start`, `page_end`, and **`char_start`/`char_end` into the document's full extracted text**.

Those character offsets are the load-bearing detail. They are what let the UI highlight the exact source span rather than saying "somewhere in document 3." They cost one integer pair at ingest time and are impossible to reconstruct later.

**Embeddings are cached by SHA-256 of chunk text**, so re-uploading the same document costs nothing.

---

## 4. Retrieval

**Hybrid: dense + lexical, fused with Reciprocal Rank Fusion.**

- **Dense:** cosine similarity over `vector(1536)`, HNSW index. Good at paraphrase and conceptual matching.
- **Lexical:** Postgres full-text search — a generated `tsvector` column, GIN index, `websearch_to_tsquery` + `ts_rank_cd`. Good at exactly what embeddings are worst at: identifiers, acronyms, product names, numbers, rare proper nouns.
- **Fusion:** RRF, `score = Σ 1/(k + rank_i)` with `k=60`. Rank-based, so I don't have to calibrate two incomparable score scales against each other.

Take top 20 from each arm, fuse, keep the **top 8** as context (~5–6k tokens). Chosen so the whole thing fits comfortably in one fast, cheap generation call.

**No reranker.** A cross-encoder would improve ordering, but it's an extra network hop, extra latency, and another provider dependency. Hybrid + RRF gets most of the benefit for a corpus this size. If evaluation shows retrieval is the bottleneck, that's the first thing I'd add.

---

## 5. Grounding, citations, and trust

This is the part I care most about.

### Inline, numbered citations

Retrieved chunks are injected into the prompt labelled `[1]`…`[8]`. The model is instructed to cite inline, per claim — `[2]`, `[3][5]` — not as a bibliography at the end. Per-claim attribution is the difference between "these documents were consulted" and "*this sentence* came from *here*."

### Structured output + server-side validation

I originally planned to parse `[n]` markers out of free prose, because streaming and structured output pull in opposite directions — you can't easily stream a JSON object *and* render tokens as they arrive. Since the gateway doesn't stream at all (§2), that tension disappeared, and I take the more robust option:

```json
{
  "answer": "The submission deadline is 30 June [2], extended from 15 June [3].",
  "citations": [2, 3],
  "insufficient_context": false,
  "assumption": null
}
```

The prose still carries **inline per-claim markers** — that's a UX decision, not a parsing one — but the machine-readable fields mean I no longer infer intent from prose. In particular `insufficient_context` is an explicit boolean instead of regexing the answer for a refusal sentence, which is a brittle thing to depend on.

**The model's output is still treated as untrusted.** On receipt the server:

1. Validates the JSON parses and has the required shape; falls back to marker-parsing if the model returns prose anyway.
2. Drops any citation index outside the range of chunks actually retrieved.
3. Cross-checks the `citations` array against markers present in `answer` — a citation claimed in neither place is discarded.
4. Maps survivors to real chunk IDs, page numbers, and character offsets.

A model that cites `[11]` when it was given eight chunks produces a dropped marker, not a broken link or a fabricated source. This is cheap, and it is the single thing that makes the citations *mean* something.

### The UI contract

Three panes: documents (left), conversation (centre), source viewer (right). Clicking a citation chip opens the source document scrolled to the cited span, **highlighted** via the stored offsets. Target: verify any claim in one click, without reading the document.

### When the answer isn't there

Two independent layers, because one is not enough:

1. **Retrieval floor (pre-LLM).** If the best dense hit is below a similarity threshold *and* the lexical arm returned nothing, the API answers "I couldn't find anything about this in your documents" **without calling the model at all.** Faster, free, and impossible for the model to override. The threshold is empirical and I'll tune it against the eval set — I'll state the number I landed on rather than pretend it's principled.
2. **Prompt contract (in-LLM).** The system prompt requires abstention when the context is insufficient, with no citations attached. Belt and braces: retrieval can pass the floor and still be irrelevant.

Abstention is rendered visually distinctly from an answer. A hedge that looks like a confident answer is worse than either.

### Ambiguous questions

The model answers the most probable reading and **states its assumption in the first line** ("Taking 'the deadline' to mean the submission deadline in §4…"). A clarifying-question round trip is better UX in principle, but it doubles latency on every ambiguous query and adds conversational state I've otherwise avoided. Noted as a deferral, not a gap I missed.

### Trust signalling

Beyond the answer, the UI shows retrieval scores per source and marks answers where retrieval was weak-but-above-floor. The user should be able to see *why* the system was confident, not just *that* it was.

---

## 6. Latency and cost

Measured budget per question: ~0.4s embedding + ~50ms retrieval + ~2–3s generation ≈ **3s to a complete answer**.

| Lever | Decision |
|---|---|
| Progressive feedback | The gateway can't stream tokens (§2), so I stream *stages* instead. The SSE connection emits the retrieved **sources within ~500ms**, and the UI renders the source panel while the answer is still generating. The user has something real to read almost immediately — which is most of the perceived-latency win that token streaming would have bought. I do **not** fake token-by-token rendering of an already-complete answer; it adds latency to simulate work that already finished. |
| Round trips | Exactly two model calls per question: one embedding (~50ms), one generation. No query rewriting, no reranking, no agentic loops. |
| Context size | 8 chunks, ~5–6k tokens. Bounded and predictable. |
| Embedding cache | Content-hashed; identical chunks never re-embed. |
| Abstention | Off-topic questions short-circuit before the LLM call — the cheapest possible path for the most common failure case. |
| Cold start | The web app pings `/health` on page load, warming the API while the user reads the upload screen. |

Every question logs latency and token counts to a `queries` table. Cheap to add at build time, impossible to reconstruct afterwards, and it means "is it fast?" and "what does it cost?" have measured answers rather than estimates.

---

## 7. Deployment topology

```
Browser
   │  (one origin)
   ▼
Vercel — Next.js
   ├── UI (RSC + client components)
   └── /api/proxy/[...path]  ──►  Railway — FastAPI  ──►  Neon Postgres + pgvector
                                       │
                                       ├──►  lexora.network   (chat)
                                       └──►  OpenAI           (embeddings)
```

**The browser never talks to the API directly.** A thin Next.js route handler proxies to FastAPI, which:

- eliminates CORS configuration entirely,
- keeps the API URL and all provider keys server-side,
- gives the reviewer a single domain,
- and still streams — the proxy passes the upstream response body straight through, Node runtime, no buffering.

Neon's **pooled** connection string is used, since the API may scale to multiple instances.

---

## 8. Explicitly out of scope

Named up front so it's clear these are decisions, not omissions:

- **Auth and multi-tenancy** — single shared workspace. The schema has no `user_id`; adding one is a migration and a filter, and pretending otherwise would be more work than it's worth here.
- **OCR / scanned PDFs** — detected and rejected with a clear message.
- **Tables, figures, images** — extracted as whatever text the parser yields. Table-aware chunking is a genuinely hard problem and out of budget.
- **Conversational memory** — each question is independent. Follow-ups like "and what about the second one?" won't resolve.
- **Multi-hop / agentic retrieval** — single retrieval pass. Questions requiring synthesis across many documents will be answered partially.
- **Document versioning** — re-upload creates a new document.
- **Background workers** — ingestion is synchronous.
- **Rate limiting and abuse protection** — none. See below.

---

## 9. What production would require

The honest list, roughly in priority order:

1. **Auth, tenancy, and per-tenant data isolation.** Row-level security or hard filtering at the query layer. This is currently a public endpoint that spends money on an API key.
2. **Rate limiting and upload quotas.** Today an anonymous user can upload until the bill hurts.
3. **Async ingestion** — a real queue with retries, backoff, and dead-lettering. Synchronous parsing does not survive a 200-page document or a provider timeout.
4. **Prompt-injection defence.** Uploaded documents are untrusted input reaching a model. A document containing "ignore previous instructions" is a live attack path. Mitigations: strict role separation, treating retrieved text as data not instructions, output validation, and never letting model output trigger actions.
5. **A real evaluation harness** — a golden set with retrieval metrics (recall@k, MRR) and answer-level grading, run in CI so retrieval changes can't silently regress.
6. **Observability** — structured logs, tracing across the two providers, alerting on abstention rate and latency percentiles. A rising abstention rate is the earliest signal that ingestion is broken.
7. **Provider failover** — both providers are single points of failure with no retry, timeout tuning, or fallback today.
8. **PII handling** — document text is stored unencrypted at rest beyond Neon's defaults, and is sent to third-party APIs. Real deployment needs a data-processing story, retention policy, and hard delete.

---

## 10. Verification

A small eval set (`evals/questions.json`, ~10 questions) covering:

- straightforward single-chunk lookups,
- facts requiring two chunks in the same document,
- **questions with no answer in the corpus** (must abstain),
- a question containing an exact identifier or acronym (the case that justifies the lexical arm).

The script asserts that expected facts appear in the answer, that citations point at the expected document, and that abstention cases actually abstain. It is not a benchmark. It exists so that "does retrieval still work after I changed the chunker?" has an answer that takes thirty seconds instead of manual clicking.
