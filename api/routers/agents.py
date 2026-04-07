import asyncio
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request

from agents.agent_store import agent_store
from api.models.responses import AgentResponse

router = APIRouter()


def _agent_to_response(agent: Dict) -> AgentResponse:
    """Convert an agent dict from the store into an AgentResponse."""
    return AgentResponse(
        id=agent["id"],
        name=agent["name"],
        description=agent["description"],
        tools=agent["tools"],
    )


@router.get("/", response_model=List[AgentResponse])
async def list_agents(request: Request):
    """List all available agents."""
    agents = await asyncio.to_thread(agent_store.list)
    return [_agent_to_response(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(request: Request, agent_id: str):
    """Get a specific agent by ID."""
    try:
        agent = await asyncio.to_thread(agent_store.get, agent_id)
        return _agent_to_response(agent)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
