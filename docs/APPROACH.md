# Approach

What we're building, in what order, and why. Detail lives in
[ARCHITECTURE.md](./ARCHITECTURE.md); sequencing and cut list live in
[PLAN.md](./PLAN.md).

---

## The problem, restated

Upload a few documents, ask questions, get answers that are **grounded** —
meaning every claim traces back to a specific passage the user can check in
one click. An answer you can't verify is worth less than no answer, so
attribution is the product, not a footnote.

## The shape

```
upload → parse → chunk → embed → store
                                   ↓
question → embed → hybrid retrieve → rank → prompt → validate → answer + citations
```

One Postgres holds documents, chunks, vectors, and the full-text index. One
FastAPI service does all the work. One Next.js app is both the UI and the
proxy in front of the API.

---

## What we're building, and why each piece exists

**1. Foundation — schema, providers, health, deploy path.** *(done)*
Everything downstream depends on the database and the two model providers, so
these get proven first. We probed both providers before writing application
code and found the chat gateway doesn't stream — which changed the design
before it cost us anything.

**2. Ingestion — bytes to chunks.**
Parse per page, pack paragraphs into ~700-token chunks with overlap, and
record page numbers **and character offsets** for every chunk. The offsets are
the load-bearing detail: they're what turn a citation from "somewhere in
document 3" into a highlighted sentence. They cost two integers at ingest time
and are impossible to reconstruct later.

**3. Retrieval — question to passages.**
Hybrid: vector similarity for paraphrase and meaning, Postgres full-text for
the things embeddings are worst at (identifiers, acronyms, names, numbers).
Fused with Reciprocal Rank Fusion, which combines rankings rather than
incomparable score scales. Both arms run in one SQL statement because the
vectors and the text live in the same table.

**4. Answering — passages to a grounded answer.**
Retrieved chunks go into the prompt numbered `[1]`…`[8]`. The model returns
JSON: the answer with inline per-claim markers, an explicit citation list, and
an `insufficient_context` flag. **We treat that output as untrusted** —
citations pointing outside the retrieved set are dropped before they ever
reach the UI. This is what makes the citations mean something rather than just
look like something.

**5. Interface — the answer and its evidence, side by side.**
Documents, conversation, source viewer. Clicking a citation opens the source
scrolled to the exact highlighted span. Abstentions render visibly differently
from answers, because a hedge that looks confident is worse than either.

**6. Evaluation — a reason to believe it works.**
A small question set covering ordinary lookups, an acronym (the case that
justifies the lexical arm), and questions with no answer in the corpus. It
exists so "did the chunker change break retrieval?" takes thirty seconds to
answer instead of manual clicking.

---

## Decisions worth defending

- **Postgres, not a vector database.** At this corpus size a dedicated vector
  store buys nothing and costs a deployment target. Keeping chunks and vectors
  in one table lets the two retrieval arms fuse in SQL.
- **Hybrid retrieval, no reranker.** Hybrid fixes the failure mode pure
  embeddings actually have. A cross-encoder would improve ordering but adds a
  hop, latency, and a third provider — the first thing to add if evaluation
  says retrieval is the bottleneck, not before.
- **Two providers, both behind interfaces.** The gateway is chat-only, so
  embeddings go direct to OpenAI. Different failure modes are exactly when you
  don't want vendor calls scattered through business logic.
- **Two independent abstention layers.** A retrieval score floor that answers
  "not in your documents" *without calling the model at all*, plus a prompt
  contract. One isn't enough: retrieval can clear the floor and still be
  irrelevant. The floor path is also the cheapest and fastest route for the
  most common failure.
- **No fake streaming.** The gateway can't stream tokens, so we stream
  retrieval *stages* instead — sources appear while the answer generates.
  Chunking an already-complete answer to look busy adds latency to simulate
  work that already finished.
- **Synchronous ingestion, no queue.** A handful of documents parses in
  seconds. A worker, a broker, and retry semantics are real infrastructure to
  deploy and debug for zero benefit at this scale.

## What we're deliberately not building

Auth and multi-tenancy, OCR for scanned PDFs (detected and rejected with a
clear message — naming the failure honestly beats handling it badly),
table-aware extraction, conversational memory, multi-hop retrieval, document
versioning, and rate limiting.

These are scope decisions, not oversights. The production gap list in
ARCHITECTURE.md §9 says what each would take, in priority order — starting
with tenancy, rate limiting, and prompt-injection defence, since uploaded
documents are untrusted input reaching a model.
