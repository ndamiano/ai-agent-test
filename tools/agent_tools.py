"""
Agent tools: list_agents, create_agent, delete_agent, save_agent.

Tools for querying and interacting with the agent registry.
Includes dynamic agent creation, lifecycle management, and cleanup.

Register at startup via register_agent_tools().
"""
import json
import logging
from typing import Optional, List

from tools.tool_manager import tool_manager
from agents.agent_store import AgentStore
from agents.agent_validator import AgentValidator
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

_agent_store = AgentStore()
_validator = AgentValidator()


def _list_agents(
    lifecycle: Optional[str] = None,
    include_metrics: bool = False,
    task_id: Optional[str] = None
) -> str:
    """
    Return the current agent roster with IDs, descriptions, and tools.

    Args:
        lifecycle: Optional filter by lifecycle ('permanent' or 'temporary')
        include_metrics: If True, include quality metrics in the response
        task_id: Auto-injected task ID (not used directly)

    Returns:
        JSON string with a list of all available agents.
    """
    # Get agents based on lifecycle filter
    if lifecycle:
        agents = _agent_store.get_agents_by_lifecycle(lifecycle)
    else:
        agents = _agent_store.list()

    roster = []
    for a in agents:
        # Maestro doesn't list itself
        if a.get("id") == "maestro":
            continue

        agent_info = {
            "id": a["id"],
            "name": a["name"],
            "description": a["description"],
            "tools": a.get("tools", []),
        }

        # Optionally include metadata and metrics
        if include_metrics:
            metadata = a.get("metadata", {})
            agent_info["lifecycle"] = metadata.get("lifecycle", "permanent")
            agent_info["is_protected"] = metadata.get("is_protected", False)
            agent_info["quality_metrics"] = metadata.get("quality_metrics", {})

        roster.append(agent_info)

    return json.dumps({"agents": roster, "count": len(roster)}, indent=2)


def _create_agent(
    name: str,
    description: str,
    system_prompt: str,
    tools: List[str],
    lifecycle: str = "temporary",
    tags: Optional[List[str]] = None,
    task_id: Optional[str] = None
) -> str:
    """
    Create a new agent dynamically.

    Args:
        name: Human-readable agent name (3-100 characters)
        description: Agent description (min 10 characters)
        system_prompt: System prompt defining agent behavior (min 50 characters)
        tools: List of tool names the agent can use
        lifecycle: 'permanent' or 'temporary' (default: temporary)
        tags: Optional list of tags for categorization
        task_id: Auto-injected task ID for context tracking

    Returns:
        JSON string with agent_id and status
    """
    try:
        # Validate inputs
        valid, error = _validator.validate_agent_name(name)
        if not valid:
            return json.dumps({"success": False, "error": error})

        # Generate unique agent ID
        agent_id = _validator.generate_agent_id(name, _agent_store)

        # Build agent specification
        agent = {
            "id": agent_id,
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "tools": tools,
            "metadata": {
                "lifecycle": lifecycle,
                "is_protected": False,
                "created_by": "agent-designer",
                "creation_context": {
                    "task_id": task_id,
                    "created_for": f"Dynamically created for task requirements"
                },
                "quality_metrics": {
                    "tasks_completed": 0,
                    "tasks_failed": 0,
                    "success_rate": 0.0,
                    "last_used_at": None,
                    "quality_score": 0.0
                },
                "auto_cleanup": {
                    "enabled": lifecycle == "temporary",
                    "min_quality_score": 40.0,
                    "max_idle_days": 30
                }
            }
        }

        # Validate complete agent spec
        valid, error = _validator.validate_agent_spec(agent)
        if not valid:
            return json.dumps({"success": False, "error": error})

        # Save to agent store
        _agent_store.save(agent)

        logger.info(f"Created new agent '{agent_id}' (lifecycle: {lifecycle})")

        return json.dumps({
            "success": True,
            "agent_id": agent_id,
            "lifecycle": lifecycle,
            "message": f"Agent '{name}' created successfully with ID '{agent_id}'"
        }, indent=2)

    except Exception as e:
        logger.error(f"Failed to create agent: {e}")
        return json.dumps({"success": False, "error": str(e)})


def _delete_agent(
    agent_id: str,
    reason: str,
    task_id: Optional[str] = None
) -> str:
    """
    Delete an agent (except protected agents).

    Args:
        agent_id: ID of the agent to delete
        reason: Reason for deletion (required for audit trail)
        task_id: Auto-injected task ID

    Returns:
        JSON string with success status
    """
    try:
        # Validate deletion is allowed
        can_delete, error = _validator.can_delete_agent(agent_id, _agent_store)
        if not can_delete:
            return json.dumps({"success": False, "error": error})

        # Delete the agent
        _agent_store.delete(agent_id)

        logger.info(f"Deleted agent '{agent_id}': {reason}")

        return json.dumps({
            "success": True,
            "agent_id": agent_id,
            "message": f"Agent '{agent_id}' deleted successfully",
            "reason": reason
        }, indent=2)

    except Exception as e:
        logger.error(f"Failed to delete agent '{agent_id}': {e}")
        return json.dumps({"success": False, "error": str(e)})


def _save_agent(
    agent_id: str,
    task_id: Optional[str] = None
) -> str:
    """
    Convert a temporary agent to permanent.

    Args:
        agent_id: ID of the agent to make permanent
        task_id: Auto-injected task ID

    Returns:
        JSON string with success status
    """
    try:
        # Get the agent
        agent = _agent_store.get_agent_by_id(agent_id)
        if not agent:
            return json.dumps({"success": False, "error": f"Agent '{agent_id}' not found"})

        # Check if already permanent
        metadata = agent.get("metadata", {})
        current_lifecycle = metadata.get("lifecycle", "permanent")

        if current_lifecycle == "permanent":
            return json.dumps({
                "success": True,
                "agent_id": agent_id,
                "message": f"Agent '{agent_id}' is already permanent",
                "already_permanent": True
            }, indent=2)

        # Update to permanent
        metadata["lifecycle"] = "permanent"
        if "auto_cleanup" in metadata:
            metadata["auto_cleanup"]["enabled"] = False

        agent["metadata"] = metadata
        _agent_store.save(agent)

        logger.info(f"Converted agent '{agent_id}' from temporary to permanent")

        return json.dumps({
            "success": True,
            "agent_id": agent_id,
            "message": f"Agent '{agent_id}' is now permanent",
            "previous_lifecycle": current_lifecycle,
            "new_lifecycle": "permanent"
        }, indent=2)

    except Exception as e:
        logger.error(f"Failed to save agent '{agent_id}': {e}")
        return json.dumps({"success": False, "error": str(e)})


def register_agent_tools() -> None:
    """Register agent tools with the global tool manager."""

    tool_manager.register_tool(
        name="list_agents",
        description=(
            "Return the current agent roster with IDs, descriptions, and available tools. "
            "Use this when planning to confirm which agents exist and what they can do. "
            "Optionally filter by lifecycle (permanent/temporary) and include quality metrics."
        ),
        parameters={
            "type": "object",
            "properties": {
                "lifecycle": {
                    "type": "string",
                    "description": "Optional filter: 'permanent' or 'temporary'"
                },
                "include_metrics": {
                    "type": "boolean",
                    "description": "If true, include quality metrics in response"
                }
            },
            "required": [],
        },
        fn=_list_agents,
    )

    tool_manager.register_tool(
        name="create_agent",
        description=(
            "Create a new specialized agent dynamically. The agent can be temporary (auto-cleanup) "
            "or permanent. Validates tool availability and generates unique ID. "
            "Returns agent_id on success."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable agent name (3-100 characters)"
                },
                "description": {
                    "type": "string",
                    "description": "Agent description explaining its purpose (min 10 characters)"
                },
                "system_prompt": {
                    "type": "string",
                    "description": "System prompt defining agent behavior and capabilities (min 50 characters)"
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool names the agent can use"
                },
                "lifecycle": {
                    "type": "string",
                    "description": "Agent lifecycle: 'permanent' or 'temporary' (default: temporary)"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization"
                }
            },
            "required": ["name", "description", "system_prompt", "tools"],
        },
        fn=_create_agent,
    )

    tool_manager.register_tool(
        name="delete_agent",
        description=(
            "Delete an agent from the registry. Protected agents (maestro, synthesizer, "
            "agent-designer) cannot be deleted. Requires a deletion reason for audit trail."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID of the agent to delete"
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for deletion (required for audit trail)"
                }
            },
            "required": ["agent_id", "reason"],
        },
        fn=_delete_agent,
    )

    tool_manager.register_tool(
        name="save_agent",
        description=(
            "Convert a temporary agent to permanent, preventing automatic cleanup. "
            "Use this when a dynamically-created agent proves useful and should be kept."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID of the agent to make permanent"
                }
            },
            "required": ["agent_id"],
        },
        fn=_save_agent,
    )

    logger.info("Agent tools registered: list_agents, create_agent, delete_agent, save_agent")