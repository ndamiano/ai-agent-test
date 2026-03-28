import asyncio
from fastapi import APIRouter
from typing import Dict, Any
from api.app import get_connector_client
from database.task_store import task_store
from agents.agent_store import agent_store
router = APIRouter()


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """
    Get system status including LM Studio connection status

    Returns:
        SystemStatus object with connection information
    """
    client = get_connector_client()
    lmstudio_connected = await client.health_check_async()

    return {
        "status": "healthy",
        "lmstudio_connected": lmstudio_connected,
        "embedding_connected": False,
        "lmstudio_url": client.base_url,
        "agent_count": len(agent_store.list()),
        "task_count": len(await asyncio.to_thread(task_store.list_tasks)),
    }
