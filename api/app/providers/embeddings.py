import httpx

from ..config import get_settings


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    """OpenAI embeddings.

    The lexora gateway is chat-completions only (no /v1/embeddings), so this
    talks to OpenAI directly. Kept behind this class so swapping providers is
    a one-file change.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._settings = get_settings()

    @property
    def dims(self) -> int:
        return self._settings.embedding_dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            resp = await self._client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                json={"model": self._settings.embedding_model, "input": texts},
                timeout=self._settings.request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        if resp.status_code != 200:
            raise EmbeddingError(f"embedding provider returned {resp.status_code}: {resp.text[:300]}")

        data = resp.json()["data"]
        # The API preserves input order, but it also returns an explicit index.
        # Sort by it rather than trusting order — cheap insurance against a
        # silent misalignment between chunks and their vectors.
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]
