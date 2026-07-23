"""Chat API — persistent conversational agent sessions.

The chat turn streams: the agent's `chat_stream` is a long, tool-using generator
(spec drafting/amending can take a while on a local model), so the endpoint pushes
each token/tool-progress event over SSE as it happens rather than blocking on the
whole turn and returning one blob.

Sessions are keyed to the authenticated user — there is no shared session. The user is
bound onto the per-user agent, which re-establishes it in the call context around each tool
call so run-creating tools attribute new runs to their owner.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.main_agent import MainAgent
from auth.deps import get_current_user, require_credits
from auth.store import User
from tools.safety import log_violation, screen_text

logger = logging.getLogger(__name__)
router = APIRouter()

# Server-side session store: user_id → MainAgent instance
_sessions: dict = {}

_REFUSAL_MESSAGE = (
    "This request can't be processed — it matches a category Maestro refuses to generate "
    "(sexual content involving minors). Mature or dark themes are fine; this specific "
    "combination is not."
)


def _get_or_create_session(user_id: str):
    if user_id not in _sessions:
        _sessions[user_id] = MainAgent(agent_id="chat", user_id=user_id)
    return _sessions[user_id]


class ChatRequest(BaseModel):
    message: str


def _sse_encode(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _chat_event_stream(agent, message: str):
    # The authed user rides on the per-user agent (`agent.user_id`), re-established around
    # each tool call — a contextvar set here would not survive Starlette driving this sync
    # generator across threadpool `next()` boundaries (each resumption gets a fresh context).
    try:
        for event in agent.chat_stream(message):
            yield _sse_encode(event)
    except Exception as e:
        logger.error(f"Chat stream error: {e}")
        yield _sse_encode({"type": "error", "message": str(e)})


@router.post("")
async def chat(request: ChatRequest, user: User = Depends(require_credits)):
    """A chat turn is free — no credits deducted, no seconds metered — but it requires a balance
    to spend. Drafting a spec the user could never afford to build is pure cost."""
    violation = screen_text(request.message)
    if violation is not None:
        log_violation(violation, user_id=user.id, source="chat")

        def _refusal_stream():
            yield _sse_encode({"type": "error", "message": _REFUSAL_MESSAGE})

        return StreamingResponse(
            _refusal_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        agent = _get_or_create_session(user.id)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return StreamingResponse(
        _chat_event_stream(agent, request.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("")
async def clear_session(user: User = Depends(get_current_user)):
    _sessions.pop(user.id, None)
    return {"cleared": user.id}
