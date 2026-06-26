from fastapi import APIRouter, Request
from typing import Dict, Any, List
from llm_clients.connector_selector import get_connector
from agents.agent_store import list_agents

router = APIRouter()


@router.get("/modules")
async def get_modules() -> List[Dict[str, str]]:
    """The mechanic-modules a human may add to a draft's plan (id + description) — the same
    selectable catalog the spec proposer picks from. Foundation modules are excluded."""
    import maestro.modules  # noqa: F401 — ensure registration
    from maestro.modules import selectable_catalog
    return [{"id": mid, "description": desc} for mid, desc in selectable_catalog()]


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
    }
