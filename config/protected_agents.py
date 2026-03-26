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
