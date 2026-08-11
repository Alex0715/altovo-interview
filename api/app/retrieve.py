"""Hybrid retrieval: dense ∪ lexical, fused with Reciprocal Rank Fusion, all
in one SQL round trip (ARCHITECTURE.md §4) — the fusion happens in Postgres,
not in Python, so there's no second pass over two score lists to get wrong.

Also owns the pre-LLM abstain check (the "retrieval floor"): if the corpus's
single best dense hit is below `min_similarity` *and* the lexical arm found
nothing at all, there is nothing worth sending to the model.
"""

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from .config import get_settings
from .models import RetrievedSource
from .providers.embeddings import Embedder

_QUERY = """
WITH params AS (
    SELECT $1::vector AS qvec, websearch_to_tsquery('english', $2) AS qts
),
dense AS (
    SELECT c.id,
           1 - (c.embedding <=> p.qvec) AS similarity,
           row_number() OVER (ORDER BY c.embedding <=> p.qvec) AS rnk
    FROM chunks c, params p
    WHERE c.embedding IS NOT NULL
      AND ($3::uuid[] IS NULL OR c.document_id = ANY($3::uuid[]))
    ORDER BY c.embedding <=> p.qvec
    LIMIT $4
),
lexical AS (
    SELECT c.id,
           ts_rank_cd(c.tsv, p.qts) AS lex_score,
           row_number() OVER (ORDER BY ts_rank_cd(c.tsv, p.qts) DESC) AS rnk
    FROM chunks c, params p
    WHERE p.qts::text <> ''
      AND c.tsv @@ p.qts
      AND ($3::uuid[] IS NULL OR c.document_id = ANY($3::uuid[]))
    ORDER BY lex_score DESC
    LIMIT $4
),
-- Reciprocal Rank Fusion: rank-based, not score-based, so the incomparable
-- cosine-similarity and ts_rank_cd scales never need to be calibrated
-- against each other. score = sum(1 / (k + rank)) across the arms a chunk
-- appears in.
fused AS (
    SELECT COALESCE(d.id, l.id)                       AS chunk_id,
           COALESCE(1.0 / ($5 + d.rnk), 0)
             + COALESCE(1.0 / ($5 + l.rnk), 0)         AS rrf_score,
           d.rnk                                       AS dense_rank,
           l.rnk                                       AS lexical_rank
    FROM dense d
    FULL OUTER JOIN lexical l ON d.id = l.id
)
SELECT
    c.id           AS chunk_id,
    c.document_id  AS document_id,
    doc.filename   AS filename,
    c.content      AS content,
    c.page_start   AS page_start,
    c.page_end     AS page_end,
    c.char_start   AS char_start,
    c.char_end     AS char_end,
    f.rrf_score    AS rrf_score,
    f.dense_rank   AS dense_rank,
    f.lexical_rank AS lexical_rank,
    d.similarity   AS dense_similarity,
    -- Corpus-wide floor signal, independent of whether this particular row
    -- survived the top-K cut below.
    (SELECT max(similarity) FROM dense) AS corpus_best_similarity,
    (SELECT count(*) FROM lexical)      AS corpus_lexical_hits
FROM fused f
JOIN chunks c    ON c.id = f.chunk_id
JOIN documents doc ON doc.id = c.document_id
LEFT JOIN dense d ON d.id = f.chunk_id
ORDER BY f.rrf_score DESC
LIMIT $6
"""


@dataclass
class RetrievalResult:
    sources: list[RetrievedSource]
    floor_tripped: bool
    best_similarity: float
    lexical_hits: int


async def retrieve(
    pool: asyncpg.Pool,
    embedder: Embedder,
    question: str,
    *,
    document_ids: list[UUID] | None = None,
) -> RetrievalResult:
    settings = get_settings()
    query_vector = await embedder.embed_one(question)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _QUERY,
            query_vector,
            question,
            document_ids,
            settings.retrieval_candidates,
            settings.rrf_k,
            settings.retrieval_top_k,
        )

    if not rows:
        return RetrievalResult(sources=[], floor_tripped=True, best_similarity=0.0, lexical_hits=0)

    best_similarity = float(rows[0]["corpus_best_similarity"] or 0.0)
    lexical_hits = int(rows[0]["corpus_lexical_hits"] or 0)
    floor_tripped = best_similarity < settings.min_similarity and lexical_hits == 0

    sources = [
        RetrievedSource(
            marker=i,
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            filename=r["filename"],
            page_start=r["page_start"],
            page_end=r["page_end"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            snippet=r["content"],
            score=float(r["rrf_score"]),
            dense_rank=r["dense_rank"],
            lexical_rank=r["lexical_rank"],
        )
        for i, r in enumerate(rows, start=1)
    ]

    return RetrievalResult(
        sources=sources,
        floor_tripped=floor_tripped,
        best_similarity=best_similarity,
        lexical_hits=lexical_hits,
    )
