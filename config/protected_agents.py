"""
Protected Agents Configuration

Defines the set of core system agents that cannot be deleted.
These agents are essential for system operation and are protected
from accidental or malicious deletion.
"""

# Core system agents that cannot be deleted
PROTECTED_AGENTS = {
    "maestro",      # Master orchestrator
    "synthesizer",  # Final output synthesizer
    "worker"        # Generic worker agent
}


def is_protected_agent(agent_id: str) -> bool:
    """
    Check if an agent is protected from deletion.

    Args:
        agent_id: The unique identifier of the agent to check

    Returns:
        True if the agent is protected, False otherwise
    """
    return agent_id in PROTECTED_AGENTS
