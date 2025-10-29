from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.services.job_events import job_event_broker

router = APIRouter(prefix="/chat", tags=["chat"], include_in_schema=False)


@router.get("/stream")
async def chat_stream(conversation_id: str = Query(..., min_length=3, max_length=128)) -> StreamingResponse:
    """Server-sent events stream carrying ingestion job updates for chat clients."""

    queue = await job_event_broker.subscribe(conversation_id)

    async def event_generator() -> AsyncIterator[str]:
        try:
            while True:
                payload = await queue.get()
                data = json.dumps(payload)
                yield f"event: job_update\ndata: {data}\n\n"
        except asyncio.CancelledError:  # pragma: no cover - client disconnected
            raise
        finally:
            job_event_broker.unsubscribe(conversation_id, queue)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
