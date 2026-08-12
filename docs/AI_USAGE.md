# AI usage note

This was built with Claude Code, conversationally, over the ~8-hour budget in `docs/PLAN.md`. The
running log kept during the build — one entry per moment the model's default was wrong and I
overrode it — is `docs/ai-log.md`. This note is the synthesized version: what actually mattered,
for a reader who won't read the full log.

## Where it helped

Most of the build was the model executing a design I'd already made the risky calls on (H0's
provider probe, the schema, the RRF/offset invariants called out in `docs/PLAN.md`'s "candidates
worth watching" list). Boilerplate — FastAPI routing, Pydantic contracts, the SSE proxy, the React
shell — is where an assistant earns its keep with the least supervision needed, and that's most of
the line count in this repo.

## Where it got something wrong, and what I did about it

Three moments actually mattered — full detail in `docs/ai-log.md`:

1. **The design assumed token streaming the provider doesn't support (H0).** The first architecture
   pass specified `event: token` SSE chunks end to end, a reasonable default for an
   "OpenAI-compatible" gateway. Probing before writing code showed `stream: true` is accepted and
   *silently ignored* — no error, just a complete non-chunked response. Caught at hour 0 instead of
   hour 8, which is the only reason it was a design amendment (stream stages, not tokens) instead of
   a late rewrite of the SSE layer and the chat UI together.

2. **The chunker's overlap logic could hang forever (H1).** The carry-forward loop's budget check
   could trigger before consuming any new paragraph, if the carried paragraph was itself
   at-or-over the token target — an interaction with the oversize-paragraph fallback the model
   didn't account for on the first pass. Caught by running the chunker against a no-blank-line
   input immediately after writing it (not deferred to integration testing), not by reading the
   code. It hung at 99% CPU in seconds; the fix guarantees the loop's index advances every
   iteration regardless of how large a carried element is.

3. **The first eval run's failures were the eval's fault, not the app's (H5).** Two of four
   "failures" asked for facts that only lived in `api/app/config.py`, which wasn't part of the
   ingested eval corpus — the model correctly abstained on those rather than inventing a number.
   The other two failed on overly literal keyword matching (`"forward progress"` vs. the model's
   own correct paraphrase). The fix was to the eval, not the app: an eval failure is a claim about
   either the app or the eval, and the abstain behavior was the signal that pointed at which one.

## The transferable pattern

All three are the same shape: a plausible default (streaming works, the loop terminates, a
question is answerable from the corpus) that was wrong in a way that produces no error — a silent
mismatch, a silent hang, a silent wrong assumption baked into a test. In all three cases the fix
was "run it and look," not "read it more carefully" — probing the provider, running the chunker on
an adversarial input immediately after writing it, and treating an eval failure as ambiguous
between two possible culprits until checked. None of these would have been caught by a more
careful read of the generated code; they needed the thing actually executed against a case shaped
like the failure mode.
