"""Parse → chunk → embed. Synchronous, run inline on the upload request (see
ARCHITECTURE.md §3 — no queue, no worker for a corpus this size).

Everything here is pure/testable except `ingest_document`, which is the only
function that touches the DB or a provider; it owns the status-transition
state machine (parsing → embedding → ready | failed) and never raises —
failures are recorded on the document row instead.
"""

import hashlib
import io
import re
from dataclasses import dataclass
from uuid import UUID

import asyncpg
import tiktoken
from pypdf import PdfReader

from . import repo
from .config import get_settings
from .providers.embeddings import Embedder

_ENCODING = tiktoken.get_encoding("cl100k_base")

PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


class ScannedPDFError(ValueError):
    """Raised when a PDF yields no extractable text — almost certainly a
    scanned/image-only document. Detecting this honestly matters more than
    silently ingesting an empty document (ARCHITECTURE.md §3)."""


class UnsupportedFileTypeError(ValueError):
    pass


def detect_mime_type(filename: str, content_type: str | None) -> str | None:
    lower = filename.lower()
    if lower.endswith(".pdf") or content_type == "application/pdf":
        return "application/pdf"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return "text/markdown"
    if lower.endswith(".txt") or content_type == "text/plain":
        return "text/plain"
    return None


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


# ---------------------------------------------------------------------------
# Parsing: bytes -> per-page text
# ---------------------------------------------------------------------------


def parse_pages(mime_type: str, data: bytes) -> list[str]:
    """Returns one string per page. Non-paginated formats are a single page."""
    if mime_type == "application/pdf":
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        if not any(p.strip() for p in pages):
            raise ScannedPDFError(
                "this looks like a scanned document; OCR isn't supported"
            )
        return pages

    if mime_type in ("text/plain", "text/markdown"):
        text = data.decode("utf-8", errors="replace")
        return [text]

    raise UnsupportedFileTypeError(f"unsupported mime type: {mime_type}")


# ---------------------------------------------------------------------------
# full_text + paragraph offsets
# ---------------------------------------------------------------------------


@dataclass
class Paragraph:
    page_num: int
    char_start: int
    char_end: int
    text: str


def build_full_text(pages: list[str]) -> tuple[str, list[Paragraph]]:
    """Concatenates pages into one string (source of truth for citation
    highlighting) and splits them into paragraphs, computing char offsets
    *while building* full_text rather than searching for them afterwards —
    that's what guarantees full_text[char_start:char_end] == text by
    construction, not by luck."""
    parts: list[str] = []
    paragraphs: list[Paragraph] = []
    offset = 0

    for page_num, page_text in enumerate(pages, start=1):
        for raw in PARAGRAPH_SPLIT_RE.split(page_text.strip()):
            para = raw.strip()
            if not para:
                continue
            if parts:
                sep = "\n\n"
                parts.append(sep)
                offset += len(sep)
            start = offset
            parts.append(para)
            offset += len(para)
            paragraphs.append(Paragraph(page_num, start, offset, para))

    return "".join(parts), paragraphs


def _split_oversized_paragraph(paragraph: Paragraph, max_tokens: int) -> list[Paragraph]:
    """A paragraph alone bigger than the chunk target (rare: no-blank-line
    text dumps, huge tables-as-text). Hard-split on whitespace boundaries at
    an approximate char/token ratio rather than pulling in a sentence
    tokenizer for an edge case."""
    approx_chars_per_token = 4
    window = max_tokens * approx_chars_per_token
    text = paragraph.text
    pieces: list[Paragraph] = []
    start = 0
    while start < len(text):
        end = min(start + window, len(text))
        if end < len(text):
            while end < len(text) and not text[end].isspace():
                end += 1
        pieces.append(
            Paragraph(
                paragraph.page_num,
                paragraph.char_start + start,
                paragraph.char_start + end,
                text[start:end],
            )
        )
        start = end
    return pieces


# ---------------------------------------------------------------------------
# Chunking: pack paragraphs to ~target tokens, ~overlap tokens carried over
# ---------------------------------------------------------------------------


@dataclass
class ChunkDraft:
    ordinal: int
    content: str
    content_hash: str
    token_count: int
    page_start: int
    page_end: int
    char_start: int
    char_end: int


def chunk_paragraphs(
    full_text: str,
    paragraphs: list[Paragraph],
    *,
    target_tokens: int,
    overlap_tokens: int,
) -> list[ChunkDraft]:
    # Never split a paragraph unless it alone would blow the chunk budget.
    expanded: list[Paragraph] = []
    for p in paragraphs:
        if count_tokens(p.text) > target_tokens * 2:
            expanded.extend(_split_oversized_paragraph(p, target_tokens))
        else:
            expanded.append(p)

    drafts: list[ChunkDraft] = []
    carry: list[Paragraph] = []
    idx = 0
    n = len(expanded)

    while idx < n:
        group = list(carry)
        group_tokens = sum(count_tokens(p.text) for p in group)
        advanced = False  # has this round consumed a *new* paragraph yet?

        while idx < n:
            p = expanded[idx]
            p_tokens = count_tokens(p.text)
            # Only the budget check can stop us before adding — and only
            # once we've made forward progress. Otherwise a carried-over
            # paragraph that's already >= target_tokens on its own (the
            # oversize-split fallback) would break here immediately, idx
            # never advances, and the next chunk reproduces the same carry
            # forever.
            if advanced and group_tokens + p_tokens > target_tokens:
                break
            group.append(p)
            group_tokens += p_tokens
            idx += 1
            advanced = True

        char_start = group[0].char_start
        char_end = group[-1].char_end
        content = full_text[char_start:char_end]

        # The load-bearing invariant: citation highlighting depends on this
        # holding for every chunk, forever. Fail loud at ingest time, not
        # silently at click time.
        assert full_text[char_start:char_end] == content

        drafts.append(
            ChunkDraft(
                ordinal=len(drafts),
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                token_count=count_tokens(content),
                page_start=min(p.page_num for p in group),
                page_end=max(p.page_num for p in group),
                char_start=char_start,
                char_end=char_end,
            )
        )

        if idx >= n:
            break

        # Carry trailing paragraphs worth ~overlap_tokens into the next chunk.
        carry = []
        carry_tokens = 0
        for p in reversed(group):
            p_tokens = count_tokens(p.text)
            if carry and carry_tokens + p_tokens > overlap_tokens:
                break
            carry.insert(0, p)
            carry_tokens += p_tokens

    return drafts


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def ingest_document(
    pool: asyncpg.Pool,
    embedder: Embedder,
    document_id: UUID,
    mime_type: str,
    data: bytes,
) -> None:
    """Drives the document through parsing → embedding → ready|failed.
    Never raises — any failure is recorded on the document row so the
    endpoint can always return a final status."""
    settings = get_settings()
    try:
        pages = parse_pages(mime_type, data)
        full_text, paragraphs = build_full_text(pages)
        drafts = chunk_paragraphs(
            full_text,
            paragraphs,
            target_tokens=settings.chunk_target_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )
        if not drafts:
            raise ValueError("no extractable text after chunking")

        async with pool.acquire() as conn:
            await repo.set_parsed(
                conn, document_id, full_text=full_text, page_count=len(pages)
            )

            cache = await repo.cached_embeddings(
                conn, [d.content_hash for d in drafts]
            )
            to_embed = [d for d in drafts if d.content_hash not in cache]
            if to_embed:
                vectors = await embedder.embed([d.content for d in to_embed])
                for d, vec in zip(to_embed, vectors):
                    cache[d.content_hash] = vec

            await repo.insert_chunks(
                conn,
                document_id,
                [
                    {
                        "ordinal": d.ordinal,
                        "content": d.content,
                        "content_hash": d.content_hash,
                        "token_count": d.token_count,
                        "page_start": d.page_start,
                        "page_end": d.page_end,
                        "char_start": d.char_start,
                        "char_end": d.char_end,
                        "embedding": cache[d.content_hash],
                    }
                    for d in drafts
                ],
            )
            await repo.set_status(conn, document_id, "ready")

    except Exception as exc:  # noqa: BLE001 — status transition, not a crash
        async with pool.acquire() as conn:
            await repo.set_status(conn, document_id, "failed", error=str(exc))
