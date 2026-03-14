"""
Agent tools: list_agents.

Tools for querying and interacting with the agent registry.
When the Agent Creator lands, its tools live here too.

Register at startup via register_agent_tools().
"""
import json
import logging

from tools.tool_manager import tool_manager
from agents.agent_store import AgentStore

logger = logging.getLogger(__name__)

_agent_store = AgentStore()


def _list_agents() -> str:
    """
    Return the current agent roster with IDs, descriptions, and tools.

    Returns:
        JSON string with a list of all available agents.
    """
    agents = _agent_store.list()
    roster = [
        {
            "id": a["id"],
            "name": a["name"],
            "description": a["description"],
            "tools": a.get("tools", []),
        }
        for a in agents
        if a.get("id") != "maestro"  # Maestro doesn't list itself
    ]
    return json.dumps({"agents": roster, "count": len(roster)})


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

    logger.info("Agent tools registered: list_agents")