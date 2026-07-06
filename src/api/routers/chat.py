"""Chat API — persistent conversational agent sessions.

The chat turn streams: the agent's `chat_stream` is a long, tool-using generator
(spec drafting/amending can take a while on a local model), so the endpoint pushes
each token/tool-progress event over SSE as it happens rather than blocking on the
whole turn and returning one blob.
"""

import json
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# Server-side session store: session_id → MainAgent instance
_sessions: dict = {}


def _get_or_create_session(session_id: str):
    if session_id not in _sessions:
        from agents.main_agent import MainAgent
        _sessions[session_id] = MainAgent(agent_id="chat")
    return _sessions[session_id]


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


def _sse_encode(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _chat_event_stream(agent, message: str):
    try:
        for event in agent.chat_stream(message):
            yield _sse_encode(event)
    except Exception as e:
        logger.error(f"Chat stream error: {e}")
        yield _sse_encode({"type": "error", "message": str(e)})


@router.post("")
async def chat(request: ChatRequest):
    try:
        agent = _get_or_create_session(request.session_id)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return StreamingResponse(
        _chat_event_stream(agent, request.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/{session_id}")
async def clear_session(session_id: str):
    if session_id in _sessions:
        del _sessions[session_id]
    return {"cleared": session_id}
