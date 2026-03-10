from fastapi import APIRouter
from typing import Dict, Any
from connectors.lmstudio_client import LMStudioConnector
from connectors.embedding_client import EmbeddingClient
from api.app import lmstudio_client, embedding_client

router = APIRouter()

@router.get("/health")
async def get_health():
    return {"message": "System health endpoint - implementation pending"}

@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """
    Get system status including LM Studio and embedding client connection status
    
    Returns:
        SystemStatus object with connection information
    """
    # Check LM Studio connection status
    lmstudio_connected = lmstudio_client.health_check()
    
    # Check embedding client connection status
    embedding_connected = embedding_client.health_check()
    
    return {
        "status": "healthy",
        "lmstudio_connected": lmstudio_connected,
        "embedding_connected": embedding_connected,
        "lmstudio_url": lmstudio_client.base_url,
        "agent_count": 0,  # TODO: Implement agent counting
        "task_count": 0,   # TODO: Implement task counting
    }

@router.post("/restart")
async def restart_system():
    return {"message": "System restart endpoint - implementation pending"}

@router.post("/shutdown")
async def shutdown_system():
    return {"message": "System shutdown endpoint - implementation pending"}
