import os
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import repo
from .config import get_settings
from .db import close_pool, create_pool, get_pool
from .ingest import detect_mime_type, ingest_document
from .models import DocumentDetail, DocumentSummary
from .providers.embeddings import Embedder
from .providers.llm import LLMClient


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


if __name__ == "__main__":
    import uvicorn

    # Bind 0.0.0.0 and honour $PORT — Railway assigns the port and health
    # checks fail if the app hardcodes one or binds to localhost.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
