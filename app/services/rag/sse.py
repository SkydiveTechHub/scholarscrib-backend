"""SSE helpers (JSON event stream, similar in spirit to Ninja SSE)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi.responses import StreamingResponse

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def format_sse(event: str, data: Mapping[str, Any] | list | str | None) -> str:
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data if data is not None else {}, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def sse_response(
    events: AsyncIterator[dict[str, Any]],
) -> StreamingResponse:
    async def body():
        async for item in events:
            yield format_sse(item["event"], item.get("data"))

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
