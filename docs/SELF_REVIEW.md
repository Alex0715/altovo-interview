# Self-review

Written as the PR description this would carry, if it were opened as a PR rather than pushed
straight to `main`.

## What this is

Grounded document Q&A: upload PDFs/text/markdown, ask questions, get answers where every claim
carries an inline citation that resolves to a highlighted span in the source document. Full design
rationale in `docs/ARCHITECTURE.md`; what's covered and what's deliberately out is in `docs/ARCHITECTURE.md` §8.

Live: API on Railway, seeded with this repo's own docs (`docs/AI_USAGE.md` explains why — it's a
self-contained, fact-checkable corpus). Web app not deployed yet; see `README.md#deployment`.

## Trade-offs, and why I made them

- **Synchronous ingestion, no queue.** A parse-and-embed request blocks until done. Fine at a
  handful-of-documents scale (seconds), and it removes a worker, a broker, and retry semantics from
  the system entirely. Wrong call the moment someone uploads a 200-page PDF or a provider call
  hangs — that request just sits there. Traded system complexity for a real ceiling on request
  latency; documented as a deferral in §9 of `docs/ARCHITECTURE.md`, not a gap I didn't see.
- **No reranker.** Hybrid (dense + lexical) RRF gets most of the benefit a cross-encoder would add,
  for one fewer network hop and provider dependency. If the eval showed retrieval as the bottleneck
  this is the first thing I'd add — it didn't, at this corpus size.
- **Rank-based fusion (RRF), not score-based.** Cosine similarity and `ts_rank_cd` live on
  incomparable scales; fusing ranks sidesteps calibrating them against each other, at the cost of
  losing the raw score's magnitude information (a rank-1 hit at similarity 0.95 and one at 0.31
  fuse identically).
- **Original files are never persisted**, only extracted text. Simplifies deletion to one
  `DELETE ... CASCADE` and removes blob storage from the system, at the cost of no re-parsing with
  a better extractor later without re-uploading.
- **Two independent abstain layers** (retrieval floor pre-LLM, prompt contract in-LLM) instead of
  one. More code than trusting either alone, but the floor is the cheap, fast, unoverridable path
  for the common case, and the prompt contract catches the case where retrieval clears the floor but
  the passages are still off-topic.

## Known weaknesses

- **`min_similarity = 0.25` is tuned against a 4-document, ~15-chunk corpus** (`docs/AI_USAGE.md`).
  It's the right number for what I could evaluate against in the time available, not a number I'd
  defend at a materially different corpus size or domain without re-running the eval there.
- **No conversational memory.** Every question is independent; "what about the second one?" doesn't
  resolve. A real chat product needs this; it was cut to keep state out of the system for an 8-hour
  build (`docs/ARCHITECTURE.md` §8).
- **No auth, no tenancy, no rate limiting.** The schema has no `user_id`. This is a public endpoint
  that spends real API-key money and currently has no floor on abuse. Priority-one item if this went
  anywhere near production — see `docs/ARCHITECTURE.md` §9.
- **No prompt-injection defence beyond structured output.** Uploaded documents are untrusted input
  reaching a model; a document containing "ignore previous instructions" is a live attack surface I
  haven't hardened against beyond treating retrieved text as data, not instructions, in the prompt.
- **The eval set is small and hand-written (10 questions), not a golden set with retrieval metrics**
  (recall@k, MRR). It answers "did this obviously break" in thirty seconds; it would not catch a
  retrieval regression that's real but subtle.
- **Vercel deploy is still pending** at time of writing — the API round-trip is verified end-to-end
  (`README.md`'s health check, plus live citation-validated answers against the seeded corpus), but
  the full three-pane UI hasn't been exercised against the production API yet.

## What I'd do with another week

In priority order, matching `docs/ARCHITECTURE.md` §9:

1. Auth + per-tenant isolation — the schema change is small (`user_id` + a filter everywhere), the
   review-for-correctness is not.
2. Async ingestion with retries and backoff, so a slow provider or a big document degrades instead
   of blocking a request.
3. Prompt-injection hardening and a rate limit, before this is reachable by anyone but me.
4. A real eval harness — golden set, recall@k/MRR on retrieval specifically (decoupled from
   generation quality), run in CI so a retrieval change can't silently regress without a human
   noticing.
5. Reranking, if a bigger/more realistic corpus shows hybrid + RRF alone isn't ordering well enough
   — I'd want the eval evidence for this before adding the dependency, not before.
