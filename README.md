# Altovo Document Q&A

Upload documents, ask questions, get answers grounded in inline, clickable citations — click a
citation and the source pane scrolls to and highlights the exact passage it came from.

**Live API:** https://altovo-interview-production.up.railway.app (Railway, seeded with this repo's
own `docs/*.md` — ask it things like *"what does RRF stand for and what k value does it use?"*)
**Live app (Vercel):** not deployed yet — see [Deployment](#deployment).

Design rationale lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (long form) and
[`docs/APPROACH.md`](docs/APPROACH.md) (short form). Build sequencing and the cut list are in
[`docs/PLAN.md`](docs/PLAN.md). Where the model got something wrong mid-build: [`docs/ai-log.md`](docs/ai-log.md)
/ [`docs/AI_USAGE.md`](docs/AI_USAGE.md). Trade-offs and known weaknesses:
[`docs/SELF_REVIEW.md`](docs/SELF_REVIEW.md).

---

## Stack

Next.js (App Router) on Vercel → same-origin proxy route → FastAPI on Railway → Neon Postgres
(`pgvector` + full-text search, one store for both retrieval arms). Chat via `lexora.network`,
embeddings direct to OpenAI (`text-embedding-3-small`). Details and the "why" for each: §2 of
`docs/ARCHITECTURE.md`.

```
altovo-docqa/
├── web/                  # Next.js — deployed to Vercel
├── api/                  # FastAPI — deployed to Railway
│   ├── app/
│   └── migrations/001_init.sql
├── evals/questions.json  # eval set
├── scripts/eval.py       # runs the eval set against a live /ask
└── docs/                 # architecture, plan, AI-usage log, self-review
```

---

## Local setup

Requires Python 3.12+, Node 20+, and a Postgres instance with the `vector` extension available
(Neon's free tier works — enable it under the project's Extensions tab, or the migration does it
for you with `CREATE EXTENSION IF NOT EXISTS vector`).

### 1. Database

```bash
psql "$DATABASE_URL" -f api/migrations/001_init.sql
```

Idempotent — safe to re-run. Use Neon's **pooled** connection string (host contains `-pooler`);
`api/app/db.py` disables asyncpg's prepared-statement cache to match.

### 2. API

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, OPENAI_API_KEY, LEXORA_API_KEY
python -m app.main     # binds 0.0.0.0:8000, or $PORT if set
```

`GET http://localhost:8000/health` should return `{"status": "ok", "checks": {"database": "ok"}}`.

### 3. Web

```bash
cd web
npm install
cp .env.example .env.local   # API_BASE_URL=http://127.0.0.1:8000
npm run dev                  # http://localhost:3000
```

The browser never talks to the API directly — every request goes through
`web/app/api/proxy/[...path]/route.ts`, which forwards to `API_BASE_URL` and passes the SSE body
through unbuffered. This is also why there's no CORS config on the API side beyond `CORS_ORIGINS`
(only needed if something ever calls the API directly, e.g. `scripts/eval.py`).

---

## Environment variables

### `api/.env`

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Neon **pooled** connection string, `sslmode=require` |
| `OPENAI_API_KEY` | yes | — | Embeddings only — the chat gateway is chat-completions-only |
| `LEXORA_API_KEY` | yes | — | Chat generation |
| `LEXORA_BASE_URL` | no | `https://api.lexora.network` | |
| `LEXORA_MODEL` | no | `gpt-5.4-mini` | |
| `EMBEDDING_MODEL` | no | `text-embedding-3-small` | |
| `EMBEDDING_DIMS` | no | `1536` | Must match the `vector(1536)` column if changed |
| `CHUNK_TARGET_TOKENS` / `CHUNK_OVERLAP_TOKENS` | no | `700` / `100` | |
| `RETRIEVAL_CANDIDATES` / `RETRIEVAL_TOP_K` | no | `20` / `8` | Per-arm candidates before fusion / chunks sent to the model |
| `RRF_K` | no | `60` | Reciprocal Rank Fusion constant |
| `MIN_SIMILARITY` | no | `0.25` | Pre-LLM abstain floor — validated against `evals/questions.json`, see `docs/AI_USAGE.md` |
| `MAX_UPLOAD_BYTES` | no | `20971520` (20 MB) | |
| `CORS_ORIGINS` | no | `http://localhost:3000` | Comma-separated |

### `web/.env.local`

| Variable | Required | Default | Notes |
|---|---|---|---|
| `API_BASE_URL` | yes | `http://127.0.0.1:8000` | The Railway URL in production, set via the Vercel dashboard, **not** committed |

---

## Running the eval

```bash
python3 scripts/eval.py --api-url http://localhost:8000
```

Runs the 10 questions in `evals/questions.json` against a live `/ask`, checks abstain-correctness,
expected-keyword coverage, and citation well-formedness, and writes `evals/results.json`. Exit
code is non-zero on any failure. This is the loop used to tune `MIN_SIMILARITY` — see
`docs/AI_USAGE.md` for what that run turned up.

---

## Deployment

**API — Railway.** `api/railway.toml` builds with Railpack and starts
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set the same env vars as `api/.env` in the
Railway project settings. Currently live at
`https://altovo-interview-production.up.railway.app`.

**DB — Neon.** Run the migration once against the project's pooled connection string (see above).
The Railway deployment and local dev currently point at the same Neon database.

**Web — Vercel.** Not deployed yet. When it is: import the repo with `web/` as the project root,
set `API_BASE_URL` to the Railway URL above in the Vercel dashboard (production + preview), deploy.
No build-time env vars needed beyond that — the proxy route reads `API_BASE_URL` at request time.

---

## API contract

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness + DB check; also the cold-start warmer the web app pings on load |
| `POST` | `/documents` | multipart upload. Parses + embeds inline, returns `201 {id, status}` |
| `GET` | `/documents` | List with status and chunk counts |
| `GET` | `/documents/{id}` | Includes `full_text`, for the source viewer |
| `DELETE` | `/documents/{id}` | Cascades to chunks |
| `POST` | `/ask` | SSE. `{question, document_ids?}` → `stage` → `sources` → `stage` → `answer` → `done` (or `error`) |

Full event shapes: `docs/PLAN.md` (API contract section) and `web/lib/api.ts` (typed client, kept
in sync by hand with `api/app/models.py`).

---

## Known limitations

Single-user, no auth, synchronous ingestion, no OCR, no conversational memory — all deliberate
scope cuts for an 8-hour build, not oversights. Full list and what production would add:
`docs/ARCHITECTURE.md` §8–9. Trade-offs and what I'd do with another week: `docs/SELF_REVIEW.md`.
