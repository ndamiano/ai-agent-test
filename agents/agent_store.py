"""Agent store for managing named agent definitions as JSON files."""

import json
import logging
import pathlib
from typing import Dict, List, Optional

from config.time_utils import get_utc_timestamp
from config.protected_agents import is_protected_agent

logger = logging.getLogger(__name__)


class AgentStore:
    """
    Manages agent definitions stored as JSON files in a directory.
    Each agent is a named persona with a system prompt and tool list.
    """

    def __init__(self, store_dir: str = "config/agents"):
        self.store_dir = pathlib.Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def get(self, agent_id: str) -> Dict:
        """Load an agent definition by ID. Raises KeyError if not found."""
        file_path = self.store_dir / f"{agent_id}.json"
        if not file_path.exists():
            raise KeyError(f"Agent '{agent_id}' not found")
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def list(self) -> List[Dict]:
        """List all available agents."""
        agents = []
        for json_file in self.store_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    agents.append(json.load(f))
            except json.JSONDecodeError:
                logger.warning(f"Skipping invalid JSON file: {json_file}")
        return agents

    def save(self, agent: Dict) -> None:
        """Save an agent definition to a JSON file."""
        required_fields = ['id', 'name', 'description', 'system_prompt', 'tools']
        for field in required_fields:
            if field not in agent:
                raise ValueError(f"Agent missing required field: {field}")

        current_time = get_utc_timestamp() + "Z"
        agent['updated_at'] = current_time
        if 'created_at' not in agent:
            agent['created_at'] = current_time

        file_path = self.store_dir / f"{agent['id']}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(agent, f, indent=2, ensure_ascii=False)

    def delete(self, agent_id: str) -> None:
        """Delete an agent definition. Raises KeyError if not found."""
        if is_protected_agent(agent_id):
            raise ValueError(f"Cannot delete protected agent '{agent_id}'")
        file_path = self.store_dir / f"{agent_id}.json"
        if not file_path.exists():
            raise KeyError(f"Agent '{agent_id}' not found")
        file_path.unlink()

    def exists(self, agent_id: str) -> bool:
        """Check if an agent exists."""
        return (self.store_dir / f"{agent_id}.json").exists()