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
    def genre(self) -> str:
        """The preset name this spec was classified as ('vn' default, 'point_and_click', …).
        Kept as a UI/back-compat tag; the build actually keys off the composed `modules`."""
        return self.data.get("genre", "vn")

    @property
    def modules(self) -> List[str]:
        """The mechanic-modules this spec composes (empty until chosen). This is what the build and
        checks are driven by; the genre→modules classification happens once at propose time."""
        return list(self.data.get("modules", []))

    @property
    def params(self) -> Dict:
        """The resolved sizing knobs (>= each module's floor). The modules' get_errors read these."""
        return self.data.get("params", {})

    @property
    def substrate(self) -> str:
        """The execution substrate ('discrete' today). Explicit if set, else the preset's."""
        from maestro.modules import PRESETS
        if self.data.get("substrate"):
            return self.data["substrate"]
        preset = PRESETS.get(self.genre)
        return preset.substrate if preset else "discrete"

    @property
    def engine(self) -> str:
        """Which engine backend projects this spec's IR ('renpy' default, 'web' for the
        self-contained browser build). The IR and genre layers are engine-neutral; only the
        compile target keys off this."""
        return self.data.get("engine", "renpy")

    @property
    def frozen(self) -> bool:
        return bool(self.data.get("frozen"))

    @property
    def story_state_schema(self) -> Dict:
        return self.data.get("story_state_schema", {})
