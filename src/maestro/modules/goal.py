"""goal — the win condition, as its own composable mechanic.

`goal_flag` is the escape-room win: it DETECTS that the game has a declared, reachable win
(`goal_reachable` over `places`) and FIXES it by adding the win hotspot / declaring the goal flag
with place tools. Detector = fixer — goal owns the win check and its repair, not navigation.

"Endless" (no win) is simply the absence of a goal module — there is nothing to detect or fix, so
there is no goal_endless object. A preset that wants an open-ended game just composes no goal.
"""

from typing import Dict, List, Tuple

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.module import load_prompt
from maestro.modules.context import render_dict
from maestro.modules.module import CorrectionPrompt, Error, ErrorType, Module, register_module

_GOAL_TOOLS = ("read_place", "add_interactable", "edit_place", "set_places_meta", "read_component")


class GoalFlag(Module):
    id = "goal_flag"
    priority = 60   # after navigation has authored the places it adds the win to

    def affected_components(self) -> Tuple[str, ...]:
        return ("places",)

    def get_errors(self, context) -> List[Error]:
        if context.artifact.get("places") is None:
            return []   # no world yet — navigation builds it first
        e = checks.as_error(checks.goal_reachable(context.artifact),
                            type=ErrorType.FIX, code="goal_reachable", component="places")
        return [e] if e else []

    def get_correction_prompt(self, context, error: Error) -> CorrectionPrompt:
        rd = render_dict(context, active="places", target=error,
                         upstream_views=getattr(context, "upstream_views", {}),
                         available_tools=_GOAL_TOOLS)
        system = load_prompt("win_goal.txt")
        user = "\n".join(
            cr.spec_block(rd) + [""] + cr.todo_block(rd.get("todo", []))
            + cr.target_block(rd) + cr.upstream_block(rd.get("upstream") or {})
            + cr.tail_block(rd) + ["", "Make the one edit that makes the win reachable. Tool call only."])
        return CorrectionPrompt(system=system, user=user, allowed_tools=_GOAL_TOOLS)


FLAG = GoalFlag()
register_module(FLAG)
