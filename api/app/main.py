import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import repo
from .answer import generate_answer
from .config import get_settings
from .db import close_pool, create_pool, get_pool
from .ingest import detect_mime_type, ingest_document
from .models import AnswerPayload, AskRequest, DocumentDetail, DocumentSummary
from .providers.embeddings import Embedder
from .providers.llm import LLMClient, LLMError
from .retrieve import retrieve
from .sse import sse_event


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await create_pool()
    # One shared HTTP client: connection reuse matters when every question
    # makes two outbound provider calls.
    app.state.http = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
    app.state.embedder = Embedder(app.state.http)
    app.state.llm = LLMClient(app.state.http)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await close_pool()


app = FastAPI(title="Altovo Document Q&A", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """Liveness + dependency check. Also the cold-start warmer the web app
    pings on page load."""
    checks: dict[str, str] = {}
    try:
        async with get_pool().acquire() as conn:
            await conn.fetchval("select 1")
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — health must never raise
        checks["database"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return {"status": "ok" if healthy else "degraded", "checks": checks}


@app.post("/documents", status_code=201)
async def create_document(file: UploadFile = File(...)) -> DocumentSummary:
    settings = get_settings()
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "file exceeds max upload size")

    filename = file.filename or "untitled"
    mime_type = detect_mime_type(filename, file.content_type)
    if mime_type is None:
        raise HTTPException(415, f"unsupported file type: {filename}")

    pool = get_pool()
    async with pool.acquire() as conn:
        document_id = await repo.create_document(
            conn, filename=filename, mime_type=mime_type, byte_size=len(data)
        )

    # Synchronous, on the upload request (ARCHITECTURE.md §3). Never raises —
    # failures land as status='failed' on the document row.
    await ingest_document(pool, app.state.embedder, document_id, mime_type, data)

    async with pool.acquire() as conn:
        summary = await repo.get_summary(conn, document_id)
    return summary


@app.get("/documents")
async def list_documents() -> list[DocumentSummary]:
    async with get_pool().acquire() as conn:
        return await repo.list_documents(conn)


@app.get("/documents/{document_id}")
async def get_document(document_id: UUID) -> DocumentDetail:
    async with get_pool().acquire() as conn:
        doc = await repo.get_document(conn, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


@app.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: UUID) -> None:
    async with get_pool().acquire() as conn:
        deleted = await repo.delete_document(conn, document_id)
    if not deleted:
        raise HTTPException(404, "document not found")


async def _log_query(pool, **kwargs) -> None:
    async with pool.acquire() as conn:
        await repo.log_query(conn, **kwargs)


async def _ask_events(payload: AskRequest) -> AsyncIterator[bytes]:
    pool = get_pool()
    start = time.perf_counter()

    try:
        yield sse_event("stage", {"stage": "retrieving"})
        result = await retrieve(
            pool, app.state.embedder, payload.question, document_ids=payload.document_ids
        )

        sources_wire = [
            {
                "ordinal": s.marker,
                "chunk_id": str(s.chunk_id),
                "document_id": str(s.document_id),
                "filename": s.filename,
                "page_start": s.page_start,
                "page_end": s.page_end,
                "score": s.score,
            }
            for s in result.sources
        ]
        yield sse_event("sources", sources_wire)

        if result.floor_tripped or not result.sources:
            # Pre-LLM abstain path: faster, free, and the model never gets a
            # chance to override it (ARCHITECTURE.md §5).
            answer = AnswerPayload(
                text="I couldn't find anything about this in your documents.",
                citations=[],
                abstained=True,
                abstain_reason="no_documents" if not result.sources else "below_floor",
                assumption=None,
            )
            prompt_tokens = output_tokens = 0
        else:
            yield sse_event("stage", {"stage": "generating"})
            generated = await generate_answer(app.state.llm, payload.question, result.sources)
            answer = generated.payload
            prompt_tokens, output_tokens = generated.prompt_tokens, generated.output_tokens

        latency_ms = int((time.perf_counter() - start) * 1000)
        yield sse_event("answer", answer.model_dump(mode="json"))
        yield sse_event(
            "done",
            {"latency_ms": latency_ms, "prompt_tokens": prompt_tokens, "output_tokens": output_tokens},
        )

        await _log_query(
            pool,
            question=payload.question,
            answer=answer.text,
            retrieved_ids=[s.chunk_id for s in result.sources],
            cited_ids=[c.chunk_id for c in answer.citations],
            abstained=answer.abstained,
            abstain_reason=answer.abstain_reason,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )

    except LLMError as exc:
        yield sse_event("error", {"message": str(exc)})
    except Exception as exc:  # noqa: BLE001 — surface to the client, don't 500 mid-stream
        yield sse_event("error", {"message": f"unexpected error: {exc}"})


@app.post("/ask")
async def ask(payload: AskRequest) -> StreamingResponse:
    return StreamingResponse(_ask_events(payload), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    # Bind 0.0.0.0 and honour $PORT — Railway assigns the port and health
    # checks fail if the app hardcodes one or binds to localhost.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
