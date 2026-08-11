# Build log — where AI helped, and where I overrode it

Running notes kept during the build, for the AI-usage writeup. One entry per moment the model's default was wrong, or where I chose not to take its suggestion.

---

### H0 — The design assumed streaming that the provider doesn't support

**What the model proposed:** The initial architecture, drafted with AI assistance, specified SSE token streaming end to end — `event: token` chunks from FastAPI through the Next.js proxy into the chat UI. Entirely reasonable: lexora.network advertises an OpenAI-compatible surface, and OpenAI-compatible implies `stream: true`.

**What was actually true:** I probed the gateway before writing application code. `stream: true` is **accepted and silently ignored** — HTTP 200, `Content-Type: application/json`, one complete body, no chunking. `stream_options` is rejected outright (`400`, "property stream_options should not exist"), along with `seed`, `top_p`, `stop`, and `max_completion_tokens`. It's a validating proxy with a strict whitelist, not a passthrough.

**Why this mattered:** This is the worst shape of unsupported feature — no error to catch. A client written against the assumption would have looked broken with no signal pointing at the cause. Had I found it at hour 8 it would have meant rewriting the SSE layer and the chat rendering together.

**What I changed:**
- Dropped `token` events. Kept SSE, but for *stage* events — `sources` now lands ~500ms in so the source panel paints while generation runs. That recovers most of the perceived-latency benefit token streaming would have given.
- Explicitly rejected fake streaming (chunking an already-complete answer client-side). It adds latency to simulate work that has already finished.
- **Took the silver lining:** `response_format: {"type":"json_object"}` *is* supported. The reason I'd chosen fragile prose-marker parsing for citations was that JSON mode and streaming are incompatible. With streaming gone, that constraint evaporated — citations are now a validated JSON contract with an explicit `insufficient_context` boolean, instead of regexing prose for a refusal sentence.

**Transferable lesson:** "OpenAI-compatible" is a marketing claim, not a specification. Probe the actual parameter surface before designing against it. The 20 minutes spent on curl probes paid for itself immediately.

---

### H1 — Chunker infinite loop on a first pass, caught by actually running it

**What the model wrote first:** The paragraph-packing chunker's overlap logic carried trailing paragraphs from one chunk into the next as a head-start on the following chunk (`carry`). The inner packing loop stopped adding paragraphs once `carry + next paragraph` exceeded the token target — correct in the common case.

**What was actually wrong:** If a single paragraph (e.g. the oversize-paragraph fallback split) was itself at or above the target token count, it became `carry` on its own. Next iteration, the loop's very first check (`carry_tokens + next_paragraph > target`) broke immediately — before consuming any *new* paragraph — so `idx` never advanced and the same carry reproduced the same chunk forever.

**Why this mattered:** Would have hung the ingestion request indefinitely on any document with a long no-blank-line paragraph (e.g. a text dump, a big table-as-text) — a corpus-shape-dependent hang with no error, the same "silent failure" flavor as the H0 streaming issue. A synchronous, single-request ingestion endpoint means this is a full outage per bad upload, not a slow query.

**What I did instead:** Ran the chunker directly against a 900-word no-blank-line paragraph as soon as it was written (not deferred to integration testing) — it hung at 99% CPU within seconds. Fixed the loop to guarantee forward progress: the budget check only applies once the current round has consumed at least one *new* paragraph, so `idx` always advances regardless of how oversized a carried paragraph is. Reran against the same input plus a normal multi-paragraph/multi-page case to confirm both the fix and the offset round-trip (`full_text[char_start:char_end] == content`) hold.

**Transferable lesson:** Packing/windowing loops with a carry-forward or lookback state are exactly where "loop invariant: index advances every iteration" needs to be checked explicitly, especially once a fallback path (the oversize-paragraph split) can hand the packer an element that violates the loop's normal size assumptions.

---

### H5 — First eval pass flagged the questions, not the app

**What happened:** First `scripts/eval.py` run against the doc corpus (ARCHITECTURE.md, PLAN.md, ai-log.md, APPROACH.md ingested as the eval fixture) came back 6/10. Two of the four "failures" asked for facts (`min_similarity: 0.25`, `max_upload_bytes`) that live only in `config.py`, which wasn't part of the ingested corpus — the model correctly abstained on those sub-claims rather than inventing a number. The other two failed on overly literal keyword matching (`"forward progress"` vs. the model's `"idx advances on every iteration"`; `"ignored"` vs. `"ignores"`).

**What I changed:** Rewrote the four questions to ask only what the ingested corpus actually contains, and loosened keyword checks to short substrings (`"ignor"` matches both inflections) instead of exact phrases. Rerun: 10/10. Left `min_similarity=0.25` unchanged — both unanswerable questions abstained correctly and all eight answerable ones cited real chunks, so the default floor is already validated against this corpus rather than needing a correction.

**Transferable lesson:** An eval failure is a claim about either the app or the eval — check which before "fixing" anything. Here the model's abstain behavior was the correct signal; the eval's ground truth was wrong twice and its keyword matching was too brittle twice.

---

### H0 — zsh `path` clobbering (my own bug, noted for honesty)

A probe loop used `for path in ...`, which in zsh is bound to the `PATH` array — the first iteration destroyed `PATH` and every subsequent command failed with "command not found". Not an AI error; a shell-specific footgun worth remembering, and a reminder that a confusing failure is often the environment rather than the thing under test.
