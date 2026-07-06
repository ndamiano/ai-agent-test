"""The per-game spec — the frozen contract.

Generated fresh per game from the request: the story (title + request paragraph + story_state_schema),
the composed `modules`, and the resolved sizing `params` (each module's knob floors, with the
proposer's raises on top). The done-conditions are CODE — each module's `get_errors` enforces them
reading `params`; the spec carries no per-component check list. Once the human approves, `frozen` is
set and the build runs: "done" = every module's effective errors clear (see maestro.agent_loop).

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
    def modules(self) -> List[str]:
        """The mechanic-modules this spec composes (empty until chosen). This is what the build and
        checks are driven by; the proposer picks them directly from the module catalog."""
        return list(self.data.get("modules", []))

    @property
    def params(self) -> Dict:
        """The resolved sizing knobs (>= each module's floor). The modules' get_errors read these."""
        return self.data.get("params", {})

    @property
    def substrate(self) -> str:
        """The execution substrate ('discrete' is the only one built today)."""
        return self.data.get("substrate", "discrete")

    @property
    def engine(self) -> str:
        """Which engine backend projects this spec's IR ('renpy' default, 'godot' for
        combat/walkable-world games). The IR and genre layers are engine-neutral; only the
        compile target keys off this."""
        return self.data.get("engine", "renpy")

    @property
    def presentation(self) -> str:
        """How the walkable world renders: '2d' (default, flat sprites) or 'hd2d' (true 3D — the
        godot overworld3d presenter + feature meshes). 'hd2d' forces engine 'godot' and only
        applies to a game with a walkable `world`; the IR carries it as meta.presentation."""
        return self.data.get("presentation", "2d")

    @property
    def frozen(self) -> bool:
        return bool(self.data.get("frozen"))

    @property
    def story_state_schema(self) -> Dict:
        return self.data.get("story_state_schema", {})
