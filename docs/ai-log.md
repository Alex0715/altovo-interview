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

### H0 — zsh `path` clobbering (my own bug, noted for honesty)

A probe loop used `for path in ...`, which in zsh is bound to the `PATH` array — the first iteration destroyed `PATH` and every subsequent command failed with "command not found". Not an AI error; a shell-specific footgun worth remembering, and a reminder that a confusing failure is often the environment rather than the thing under test.
