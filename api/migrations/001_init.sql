-- Altovo Document Q&A — initial schema
-- Run: psql "$DATABASE_URL" -f api/migrations/001_init.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS documents (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  filename     text        NOT NULL,
  mime_type    text        NOT NULL,
  byte_size    integer     NOT NULL,
  page_count   integer,
  -- Source of truth for citation highlighting. Chunk char offsets index
  -- into this exact string, so it must never be re-normalised after ingest.
  full_text    text,
  status       text        NOT NULL DEFAULT 'parsing'
                 CHECK (status IN ('parsing', 'embedding', 'ready', 'failed')),
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  uuid    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal      integer NOT NULL,
  content      text    NOT NULL,
  content_hash text    NOT NULL,          -- sha256, embedding cache key
  token_count  integer NOT NULL,
  page_start   integer,
  page_end     integer,
  char_start   integer NOT NULL,          -- invariant:
  char_end     integer NOT NULL,          -- full_text[char_start:char_end] == content
  embedding    vector(1536),
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS chunks_doc_idx  ON chunks (document_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx  ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunks_hash_idx ON chunks (content_hash);

-- HNSW for cosine. NOTE: pgvector's <=> is cosine DISTANCE (0 = identical);
-- similarity is 1 - distance. Getting this backwards silently inverts ranking.
CREATE INDEX IF NOT EXISTS chunks_embed_idx
  ON chunks USING hnsw (embedding vector_cosine_ops);

-- Observability: makes "is it fast?" and "what does it cost?" measured
-- questions rather than estimates.
CREATE TABLE IF NOT EXISTS queries (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question       text        NOT NULL,
  answer         text,
  retrieved_ids  uuid[],
  cited_ids      uuid[],
  abstained      boolean     NOT NULL DEFAULT false,
  abstain_reason text,
  latency_ms     integer,
  prompt_tokens  integer,
  output_tokens  integer,
  created_at     timestamptz NOT NULL DEFAULT now()
);
