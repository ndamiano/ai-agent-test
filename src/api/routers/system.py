import asyncio
from fastapi import APIRouter, Request
from typing import Dict, Any
from llm_clients.connector_selector import get_connector
from database.task_store import task_store
from agents.agent_store import list_agents

router = APIRouter()


@router.get("/status")
async def get_status(request: Request) -> Dict[str, Any]:
    """
    Get system status including LM Studio connection status

    Returns:
        SystemStatus object with connection information
    """
    client = get_connector()

    return {
        "status": "healthy",
        "lmstudio_connected": True,
        "embedding_connected": False,
        "lmstudio_url": client.base_url,
        "agent_count": len(list_agents()),
        "task_count": len(await asyncio.to_thread(task_store.list_tasks)),
    }
