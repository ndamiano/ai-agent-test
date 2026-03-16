from fastapi import APIRouter
from typing import Dict, Any
from api.app import lmstudio_client
from database.task_store import task_store
from agents.agent_store import AgentStore

_agent_store = AgentStore()
router = APIRouter()

@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """
    Get system status including LM Studio connection status

    Returns:
        SystemStatus object with connection information
    """
    # Check LM Studio connection status
    lmstudio_connected = lmstudio_client.health_check()

    return {
        "status": "healthy",
        "lmstudio_connected": lmstudio_connected,
        "embedding_connected": False,  # Embeddings not currently used
        "lmstudio_url": lmstudio_client.base_url,
        "agent_count": len(_agent_store.list_agents()) if hasattr(_agent_store, 'list_agents') else 0,
        "task_count": len(task_store.list_tasks()),
    }
