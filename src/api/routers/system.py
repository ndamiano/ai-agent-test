from typing import Any, Dict

from fastapi import APIRouter, Request

from agents.agent_store import list_agents
from llm_clients.connector import get_connector

router = APIRouter()


@router.get("/status")
async def get_status(request: Request) -> Dict[str, Any]:
    """
    Get system status including LLM connection status

    Returns:
        SystemStatus object with connection information
    """
    client = get_connector()

    return {
        "status": "healthy",
        "llm_connected": True,
        "embedding_connected": False,
        "llm_url": client.base_url,
        "agent_count": len(list_agents()),
    }
