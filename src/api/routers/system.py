from typing import Any, Dict

from fastapi import APIRouter

from llm_clients.connector import get_connector

router = APIRouter()


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """System status: which model the queue's llm jobs are tagged for."""
    return {
        "status": "healthy",
        "llm_model": get_connector().model_name,
    }
