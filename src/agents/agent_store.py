import json
import logging
import pathlib
from typing import Dict, List

from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

_DEFAULT_STORE_DIR = pathlib.Path(__file__).parent.parent / "config" / "agents"


class AgentStore:
    def __init__(self, store_dir: pathlib.Path | str = _DEFAULT_STORE_DIR):
        self.store_dir = pathlib.Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def get(self, agent_id: str) -> Dict:
        file_path = self.store_dir / f"{agent_id}.json"
        if not file_path.exists():
            raise KeyError(f"Agent '{agent_id}' not found")
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def list(self) -> List[Dict]:
        agents = []
        for json_file in self.store_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    agents.append(json.load(f))
            except json.JSONDecodeError:
                logger.warning(f"Skipping invalid JSON file: {json_file}")
        return agents

    def save(self, agent: Dict) -> None:
        for field in ['id', 'name', 'description', 'system_prompt', 'tools']:
            if field not in agent:
                raise ValueError(f"Agent missing required field: {field}")
        now = get_utc_timestamp() + "Z"
        agent['updated_at'] = now
        if 'created_at' not in agent:
            agent['created_at'] = now
        with open(self.store_dir / f"{agent['id']}.json", 'w', encoding='utf-8') as f:
            json.dump(agent, f, indent=2, ensure_ascii=False)

    def exists(self, agent_id: str) -> bool:
        return (self.store_dir / f"{agent_id}.json").exists()


agent_store = AgentStore()
