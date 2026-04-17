"""Chat API — persistent conversational agent sessions."""

import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

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


class ChatResponse(BaseModel):
    message: str
    session_id: str


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        agent = _get_or_create_session(request.session_id)
        response = await asyncio.to_thread(agent.chat, request.message)
        return ChatResponse(message=response, session_id=request.session_id)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{session_id}")
async def clear_session(session_id: str):
    if session_id in _sessions:
        del _sessions[session_id]
    return {"cleared": session_id}
