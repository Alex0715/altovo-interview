# Build Plan

Companion to [ARCHITECTURE.md](./ARCHITECTURE.md). Ordered so that the risky, unrecoverable things happen first and the polish happens last — if I run out of time, I stop at a working deployed app and write up what's missing.

**Guiding rule:** the failure mode for this assignment is a beautiful local app and a broken link at hour 8. Deploy an empty skeleton through the whole pipeline before writing any real logic.

---

## Repo layout

```
altovo-docqa/
├── web/                        # Next.js — deployed to Vercel
│   ├── app/
│   │   ├── page.tsx            # three-pane shell
│   │   └── api/proxy/[...path]/route.ts
│   ├── components/             # DocumentList, Chat, SourceViewer, CitationChip
│   └── lib/api.ts              # typed client + SSE parsing
│
├── api/                        # FastAPI — deployed to Railway
│   ├── app/
│   │   ├── main.py             # routes, SSE
│   │   ├── config.py           # pydantic-settings
│   │   ├── db.py               # asyncpg pool
│   │   ├── models.py           # pydantic contracts
│   │   ├── ingest.py           # parse → chunk → embed
│   │   ├── retrieve.py         # hybrid search + RRF
│   │   ├── answer.py           # prompt, streaming, citation validation
│   │   └── providers/
│   │       ├── llm.py          # LLMClient  → lexora.network
│   │       └── embeddings.py   # Embedder   → OpenAI
│   ├── migrations/001_init.sql
│   └── pyproject.toml
│
├── evals/questions.json
├── scripts/eval.py
├── docs/{ARCHITECTURE,PLAN}.md
└── README.md
```

---

## Schema (`migrations/001_init.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  filename     text NOT NULL,
  mime_type    text NOT NULL,
  byte_size    integer NOT NULL,
  page_count   integer,
  full_text    text,                    -- source of truth for offset highlighting
  status       text NOT NULL DEFAULT 'parsing',   -- parsing|embedding|ready|failed
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal      integer NOT NULL,
  content      text NOT NULL,
  content_hash text NOT NULL,           -- embedding cache key
  token_count  integer NOT NULL,
  page_start   integer,
  page_end     integer,
  char_start   integer NOT NULL,        -- offsets into documents.full_text
  char_end     integer NOT NULL,
  embedding    vector(1536),
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);

CREATE INDEX chunks_doc_idx   ON chunks (document_id);
CREATE INDEX chunks_tsv_idx   ON chunks USING GIN (tsv);
CREATE INDEX chunks_embed_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- observability: makes "is it fast / what does it cost" a measured question
CREATE TABLE queries (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question       text NOT NULL,
  answer         text,
  retrieved_ids  uuid[],
  cited_ids      uuid[],
  abstained      boolean NOT NULL DEFAULT false,
  abstain_reason text,                  -- 'below_floor' | 'model_declined' | null
  latency_ms     integer,
  prompt_tokens  integer,
  output_tokens  integer,
  created_at     timestamptz NOT NULL DEFAULT now()
);
```

---

## API contract

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Also the cold-start warmer. |
| `POST` | `/documents` | multipart. Parses + embeds inline, returns `201 {id, status}`. |
| `GET` | `/documents` | List with status and chunk counts. |
| `GET` | `/documents/{id}` | Includes `full_text` for the source viewer. |
| `DELETE` | `/documents/{id}` | Cascades to chunks. |
| `POST` | `/ask` | **SSE.** `{question}` → stream. |

`/ask` event sequence:

```
event: stage      {"stage": "retrieving"}
event: sources    [{ordinal, chunk_id, document_id, filename, page_start, page_end, score}]
event: stage      {"stage": "generating"}
event: answer     {"text", "citations": [...validated...], "abstained", "assumption"}
event: done       {"latency_ms", "prompt_tokens", "output_tokens"}
event: error      {"message"}
```

**No `token` event — the gateway does not stream** (verified in H0; `stream: true` is silently ignored). The SSE connection still earns its place: `sources` lands ~500ms in, so the UI paints the source panel while the ~3s generation is still running, and `stage` drives an honest progress indicator. The answer arrives as one validated payload.

If the retrieval floor trips, the sequence short-circuits to `answer` with `abstained: true` and no model call.

---

## Hour budget (~8h)

### H0 — Deploy the skeleton first (45m) 🔴 highest risk
- [x] **Provider probe.** Neon PG18 + pgvector 0.8.1 ✅ · OpenAI `text-embedding-3-small` 1536d ✅ · lexora `POST /v1/chat/completions`, model `gpt-5.4-mini` ✅
- [x] **Found: no token streaming.** `stream:true` accepted but ignored; `seed`/`top_p`/`stop`/`stream_options` rejected `400`; `temperature`/`max_tokens`/`response_format`/`tools` OK. Design amended before writing code — see ARCHITECTURE §2, §5, §6.
- [ ] Run `001_init.sql` against Neon
- [ ] FastAPI with only `/health` → Railway, live URL
- [ ] Next.js with the proxy route → Vercel, live URL
- [ ] **Verify from a browser: Vercel page → proxy → Railway `/health` → 200**

*Nothing else starts until this round trip works.*

### H1 — Ingestion (1h15)
- [x] `POST /documents`: pypdf / plain text / markdown → per-page text
- [x] Paragraph-packing chunker, ~700 tokens / ~100 overlap, tracking page + char offsets
- [x] `Embedder` (OpenAI, batched, content-hash cache) → write chunks
- [x] Status transitions and the scanned-PDF rejection path
- [x] Sanity check: offsets round-trip — slice `full_text[char_start:char_end]` and confirm it equals `content`

### H2 — Retrieval (1h)
- [x] Single SQL statement: dense top-20 ∪ lexical top-20, RRF fused, top-8 out
- [x] Retrieval floor check for the abstain path
- [x] Eyeball results against a real document before touching the LLM

### H3 — Answering (1h30)
- [ ] System prompt: cite inline per claim, abstain when insufficient, state assumptions on ambiguity
- [ ] `LLMClient` streaming against lexora
- [ ] SSE endpoint wiring the events above
- [ ] **Citation parsing + server-side validation** (drop out-of-range markers)
- [ ] Log to `queries`

### H4 — Frontend (1h45)
- [ ] Three-pane shell; upload with drag-drop + status polling
- [ ] Streaming chat rendering; citation markers → clickable chips
- [ ] Source viewer: fetch `full_text`, scroll to and highlight `char_start..char_end`
- [ ] Distinct rendering for abstention; per-source score display
- [ ] `/health` ping on mount

### H5 — Eval + hardening (45m)
- [ ] 10 eval questions incl. 2 unanswerable and 1 acronym/identifier case
- [ ] `scripts/eval.py`, run it, **tune the retrieval floor against it**
- [ ] Error states: provider timeout, empty corpus, failed upload

### H6 — Docs + submission (1h)
- [ ] README: local setup, env vars, migration, deploy notes
- [ ] Reconcile ARCHITECTURE.md with what actually shipped (amend, don't rewrite history)
- [ ] AI-usage note (from `docs/ai-log.md`, kept during the build)
- [ ] Self-review / PR description: trade-offs, known weaknesses, next week
- [ ] Seed the deployed app with 3–4 documents so the link is useful the moment it's opened

---

## Running notes

Keep `docs/ai-log.md` open from hour zero. Every time the model gets something wrong and I override it, one line: what it did, why it was wrong, what I did instead. Two of the four written deliverables depend on these specifics, and they will be unrecoverable by hour 8.

Candidates worth watching, based on where this usually goes wrong:
- chunkers that lose character offsets (the most likely quiet break)
- `pgvector` distance operators — `<=>` is cosine *distance*; similarity is `1 - distance`, and getting this backwards silently inverts ranking
- RRF implementations that fuse scores instead of ranks
- SSE that buffers instead of streams through the Next proxy

---

## Cut list, in the order I'd cut

If time runs short, drop from the bottom up. Everything here is preferable to an undeployed app.

1. Source-span highlighting → degrade to "jump to page N"
2. Per-source score display
3. Markdown/`.txt` support → PDF only
4. Eval script → manual test log in the README
5. Delete-document endpoint

**Never cut:** deployment, citation validation, the abstain path. Those are the assignment.
