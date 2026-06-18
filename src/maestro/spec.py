"""The per-game spec — the frozen contract.

Generated fresh per game by the agent (no human-authored genre schema). A list of
components, each with a checkable done-condition the agent commits to. Once the
human approves, `frozen` is set and the build runs against it: "done" = artifact
satisfies every component's done-conditions (see maestro.validate).

Freezing is the human's out-of-band action — there is no freeze tool.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class Spec:
    data: Dict

    @property
    def title(self) -> str:
        return self.data.get("title", "")

    @property
    def request(self) -> str:
        return self.data.get("request", "")

    @property
    def frozen(self) -> bool:
        return bool(self.data.get("frozen"))

    @property
    def components(self) -> List[Dict]:
        return self.data.get("components", [])

    @property
    def story_state_schema(self) -> Dict:
        return self.data.get("story_state_schema", {})

    def dep_order(self) -> List[str]:
        """Component ids in dependency order (topological). Raises on a cycle."""
        order: List[str] = []
        seen = set()
        visiting = set()
        by_id = {c["id"]: c for c in self.components}

        def visit(cid: str):
            if cid in seen:
                return
            if cid in visiting:
                raise ValueError(f"dependency cycle through {cid!r}")
            visiting.add(cid)
            for dep in by_id.get(cid, {}).get("deps", []):
                if dep in by_id:
                    visit(dep)
            visiting.discard(cid)
            seen.add(cid)
            order.append(cid)

        for c in self.components:
            visit(c["id"])
        return order
