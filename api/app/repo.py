"""Document/chunk persistence. Kept separate from ingest.py so the parsing
and chunking logic doesn't have SQL threaded through it.
"""

from uuid import UUID

import asyncpg

from .models import DocumentDetail, DocumentSummary


async def create_document(
    conn: asyncpg.Connection, *, filename: str, mime_type: str, byte_size: int
) -> UUID:
    row = await conn.fetchrow(
        """
        INSERT INTO documents (filename, mime_type, byte_size, status)
        VALUES ($1, $2, $3, 'parsing')
        RETURNING id
        """,
        filename,
        mime_type,
        byte_size,
    )
    return row["id"]


async def set_status(
    conn: asyncpg.Connection, document_id: UUID, status: str, *, error: str | None = None
) -> None:
    await conn.execute(
        "UPDATE documents SET status = $2, error = $3 WHERE id = $1",
        document_id,
        status,
        error,
    )


async def set_parsed(
    conn: asyncpg.Connection,
    document_id: UUID,
    *,
    full_text: str,
    page_count: int,
) -> None:
    await conn.execute(
        """
        UPDATE documents
        SET full_text = $2, page_count = $3, status = 'embedding'
        WHERE id = $1
        """,
        document_id,
        full_text,
        page_count,
    )


async def insert_chunks(conn: asyncpg.Connection, document_id: UUID, chunks: list[dict]) -> None:
    """`chunks` items: ordinal, content, content_hash, token_count,
    page_start, page_end, char_start, char_end, embedding."""
    await conn.executemany(
        """
        INSERT INTO chunks (
            document_id, ordinal, content, content_hash, token_count,
            page_start, page_end, char_start, char_end, embedding
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        [
            (
                document_id,
                c["ordinal"],
                c["content"],
                c["content_hash"],
                c["token_count"],
                c["page_start"],
                c["page_end"],
                c["char_start"],
                c["char_end"],
                c["embedding"],
            )
            for c in chunks
        ],
    )


async def cached_embeddings(
    conn: asyncpg.Connection, content_hashes: list[str]
) -> dict[str, list[float]]:
    """One embedding per content_hash already stored anywhere in the corpus.
    Re-uploading an identical document costs zero embedding calls."""
    if not content_hashes:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (content_hash) content_hash, embedding
        FROM chunks
        WHERE content_hash = ANY($1) AND embedding IS NOT NULL
        """,
        content_hashes,
    )
    return {r["content_hash"]: list(r["embedding"]) for r in rows}


async def list_documents(conn: asyncpg.Connection) -> list[DocumentSummary]:
    rows = await conn.fetch(
        """
        SELECT d.id, d.filename, d.mime_type, d.byte_size, d.page_count,
               d.status, d.error, d.created_at,
               COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON c.document_id = d.id
        GROUP BY d.id
        ORDER BY d.created_at DESC
        """
    )
    return [DocumentSummary(**dict(r)) for r in rows]


async def get_document(conn: asyncpg.Connection, document_id: UUID) -> DocumentDetail | None:
    row = await conn.fetchrow(
        """
        SELECT d.id, d.filename, d.mime_type, d.byte_size, d.page_count,
               d.status, d.error, d.created_at, d.full_text,
               COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON c.document_id = d.id
        WHERE d.id = $1
        GROUP BY d.id
        """,
        document_id,
    )
    return DocumentDetail(**dict(row)) if row else None


async def get_summary(conn: asyncpg.Connection, document_id: UUID) -> DocumentSummary | None:
    row = await conn.fetchrow(
        """
        SELECT d.id, d.filename, d.mime_type, d.byte_size, d.page_count,
               d.status, d.error, d.created_at,
               COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON c.document_id = d.id
        WHERE d.id = $1
        GROUP BY d.id
        """,
        document_id,
    )
    return DocumentSummary(**dict(row)) if row else None


async def delete_document(conn: asyncpg.Connection, document_id: UUID) -> bool:
    result = await conn.execute("DELETE FROM documents WHERE id = $1", document_id)
    return result == "DELETE 1"


async def log_query(
    conn: asyncpg.Connection,
    *,
    question: str,
    answer: str | None,
    retrieved_ids: list[UUID],
    cited_ids: list[UUID],
    abstained: bool,
    abstain_reason: str | None,
    latency_ms: int,
    prompt_tokens: int,
    output_tokens: int,
) -> None:
    """Observability, not the critical path: makes 'is it fast / what does
    it cost / how often do we abstain' measured questions (ARCHITECTURE.md
    §6) instead of estimates."""
    await conn.execute(
        """
        INSERT INTO queries (
            question, answer, retrieved_ids, cited_ids,
            abstained, abstain_reason, latency_ms, prompt_tokens, output_tokens
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        question,
        answer,
        retrieved_ids,
        cited_ids,
        abstained,
        abstain_reason,
        latency_ms,
        prompt_tokens,
        output_tokens,
    )
