import json
from dataclasses import dataclass

import httpx

from ..config import get_settings


class LLMError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    output_tokens: int


class LLMClient:
    """Chat generation via the lexora.network gateway.

    Verified constraints (probed 2026-08-12, see docs/ai-log.md) — this is NOT
    a drop-in OpenAI clone, it validates against a strict parameter whitelist:

      accepted : model, messages, temperature, max_tokens, response_format, tools
      rejected : seed, top_p, stop, max_completion_tokens, stream_options  -> HTTP 400
      ignored  : stream=true  -> returns a complete JSON body, NOT an SSE stream

    That last one is silent, so do not add `stream` here expecting chunks.
    Token streaming is unavailable; the API streams retrieval *stages* instead.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._settings = get_settings()

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        json_mode: bool = False,
    ) -> Completion:
        payload: dict = {
            "model": self._settings.lexora_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            resp = await self._client.post(
                f"{self._settings.lexora_base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._settings.lexora_api_key}"},
                json=payload,
                timeout=self._settings.request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"chat request failed: {exc}") from exc

        if resp.status_code != 200:
            raise LLMError(f"chat provider returned {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected chat response shape: {json.dumps(body)[:300]}") from exc

        usage = body.get("usage") or {}
        return Completion(
            text=text,
            prompt_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
