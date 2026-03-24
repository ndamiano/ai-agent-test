"""
Agent tools: list_agents, request_agent.

Tools for querying the agent registry and logging demand for new agent types.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from tools.tool_manager import tool_manager
from agents.agent_store import AgentStore

logger = logging.getLogger(__name__)

_agent_store = AgentStore()
_DEMAND_FILE = Path("data/agent_demand.json")


def _list_agents(task_id: Optional[str] = None) -> str:
    """
    Return the current agent roster with IDs, descriptions, and tools.
    """
    agents = _agent_store.list()
    roster = []
    for a in agents:
        if a.get("id") == "maestro":
            continue
        roster.append({
            "id": a["id"],
            "name": a["name"],
            "description": a["description"],
            "tools": a.get("tools", []),
        })
    return json.dumps({"agents": roster, "count": len(roster)}, indent=2)


def _request_agent(
    capability: str,
    reason: str,
    task_id: Optional[str] = None,
    subtask_id: Optional[str] = None,
) -> str:
    """
    Log a request for a specialized agent type. This creates a demand signal
    that can be analyzed later to decide what agents are worth building manually.

    Args:
        capability: What the desired agent would do (e.g. "deep cultural analysis of fandom topics")
        reason: Why the generic worker isn't sufficient for this
        task_id: Auto-injected task ID
        subtask_id: Auto-injected subtask ID
    """
    try:
        # Load existing demand file
        if _DEMAND_FILE.exists():
            with open(_DEMAND_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {"requests": []}

        # Append new request
        data["requests"].append({
            "capability": capability,
            "reason": reason,
            "task_id": task_id,
            "subtask_id": subtask_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

        # Write back
        _DEMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEMAND_FILE, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Logged agent demand: {capability}")
        return json.dumps({
            "success": True,
            "message": f"Agent demand logged: {capability}",
            "total_requests": len(data["requests"]),
        }, indent=2)

    except Exception as e:
        logger.error(f"Failed to log agent demand: {e}")
        return json.dumps({"success": False, "error": str(e)})


def register_agent_tools() -> None:
    """Register agent tools with the global tool manager."""

    tool_manager.register_tool(
        name="list_agents",
        description=(
            "Return the current agent roster with IDs, descriptions, and available tools. "
            "Use this when planning to confirm which agents exist and what they can do."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_list_agents,
    )

    tool_manager.register_tool(
        name="request_agent",
        description=(
            "Log a request for a specialized agent type that doesn't currently exist. "
            "Use this when the generic worker would struggle with a subtask and a "
            "purpose-built agent would do better. The request is tallied for later analysis."
        ),
        parameters={
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "description": "What the desired agent would do (e.g. 'deep cultural analysis of fandom topics')"
                },
                "reason": {
                    "type": "string",
                    "description": "Why the generic worker isn't sufficient for this type of work"
                }
            },
            "required": ["capability", "reason"],
        },
        fn=_request_agent,
    )

    logger.info("Agent tools registered: list_agents, request_agent")