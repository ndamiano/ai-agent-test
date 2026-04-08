"""
Abstract base class for FSM state nodes and shared StateContext.

StateNode: Abstract base class that all state nodes inherit from
StateContext: Dataclass carrying shared state across node transitions
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from database.task_store import TaskStore
    from agents.agent_store import AgentStore


@dataclass
class StateContext:
    """
    Shared context passed between all state nodes.

    This context carries all the information needed for state transitions,
    including task identifiers, store references, and mutable state like wave count.
    """
    task_id: str
    task_store: 'TaskStore'
    agent_store: 'AgentStore'

    # Mutable state
    wave_count: int = 0
    max_waves: int = 20
    final_output: Optional[str] = None

    def increment_wave(self):
        """
        Increment wave counter and enforce safety valve.

        Raises:
            RuntimeError: If wave count exceeds max_waves
        """
        self.wave_count += 1
        if self.wave_count >= self.max_waves:
            raise RuntimeError(
                f"Task {self.task_id} exceeded maximum wave limit ({self.max_waves})"
            )


class StateNode(ABC):
    """
    Abstract base class for FSM state nodes.

    Each node represents a discrete state in the Maestro execution lifecycle.
    Nodes are stateless - all state is carried in StateContext.

    Nodes are self-contained: they own their complete logic including LLM calls,
    subtask execution, and state transitions.
    """

    @abstractmethod
    async def execute_async(self, context: StateContext) -> 'StateNode':
        """
        Execute this state's logic and return the next state.

        This method is async to enable non-blocking FSM execution.
        Use asyncio.get_event_loop().run_in_executor() to bridge with
        blocking operations like ThreadPoolExecutor.

        Args:
            context: Shared execution context

        Returns:
            Next StateNode to transition to

        Raises:
            Exception: Any errors during execution (propagated to caller)
        """
        pass

    @property
    @abstractmethod
    def state_name(self) -> str:
        """Human-readable name for this state (for logging/broadcasting)."""
        pass
