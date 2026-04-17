import json
import logging
import pathlib
from typing import Dict, List

logger = logging.getLogger(__name__)

AGENTS_DIR = pathlib.Path(__file__).parent.parent / "config" / "agents"


def get_agent(agent_id: str) -> Dict:
    path = AGENTS_DIR / f"{agent_id}.json"
    if not path.exists():
        raise KeyError(f"Agent '{agent_id}' not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_agents() -> List[Dict]:
    agents = []
    for path in AGENTS_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                agents.append(json.load(f))
        except json.JSONDecodeError:
            logger.warning(f"Skipping invalid JSON: {path}")
    return agents
