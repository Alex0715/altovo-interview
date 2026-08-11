"""Minimal SSE framing. The Next.js proxy passes the response body straight
through (Node runtime, no buffering — see ARCHITECTURE.md §7), so what's
written here is exactly what the browser's EventSource sees."""

import json
from typing import Any


def sse_event(event: str, data: Any) -> bytes:
    # default=str covers UUID/datetime if a caller ever passes a raw model
    # instead of a pre-serialized (model_dump(mode="json")) payload.
    body = json.dumps(data, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()
