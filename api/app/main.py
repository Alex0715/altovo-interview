import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import close_pool, create_pool, get_pool
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


if __name__ == "__main__":
    import uvicorn

    # Bind 0.0.0.0 and honour $PORT — Railway assigns the port and health
    # checks fail if the app hardcodes one or binds to localhost.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
