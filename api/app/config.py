from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"
    embedding_dims: int = 1536

    lexora_api_key: str
    lexora_base_url: str = "https://api.lexora.network"
    lexora_model: str = "gpt-5.4-mini"

    # Ingestion
    chunk_target_tokens: int = 700
    chunk_overlap_tokens: int = 100
    max_upload_bytes: int = 20 * 1024 * 1024

    # Retrieval
    retrieval_candidates: int = 20  # per arm, before fusion
    retrieval_top_k: int = 8  # chunks sent to the model
    rrf_k: int = 60
    # Cosine-similarity floor for the pre-LLM abstain path. Empirical —
    # tuned against evals/questions.json, see ARCHITECTURE.md §5.
    min_similarity: float = 0.25

    request_timeout_seconds: float = 60.0
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
