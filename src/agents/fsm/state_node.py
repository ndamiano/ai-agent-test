from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from database.task_store import TaskStore
    from agents.agent_store import AgentStore


@dataclass
class StateContext:
    task_id: str
    task_store: 'TaskStore'
    agent_store: 'AgentStore'
    wave_count: int = 0
    max_waves: int = 20
    final_output: Optional[str] = None

    def increment_wave(self):
        self.wave_count += 1
        if self.wave_count >= self.max_waves:
            raise RuntimeError(f"Task {self.task_id} exceeded maximum wave limit ({self.max_waves})")


class StateNode(ABC):
    @abstractmethod
    async def execute_async(self, context: StateContext) -> 'StateNode':
        pass

    @property
    @abstractmethod
    def state_name(self) -> str:
        pass
