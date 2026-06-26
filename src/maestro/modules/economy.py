"""economy — flags / variables / items + the effect/condition vocabulary.

economy owns no component of its own, but it is a full module: it DETECTS its own concern — a
flag/variable referenced by a `requires` that nothing ever sets/declares — and FIXES it, by editing
the host component the reference lives in (a node via edit_node, a place via set_places_meta). It
emits FIX errors attributed to that host and hands the loop the host's edit tools. Detector = fixer;
there is no component-ownership and no second-class "vocabulary" status.
"""

from typing import Dict, List, Tuple

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.module import load_prompt
from maestro.modules.context import render_dict
from maestro.modules.module import CorrectionPrompt, Error, ErrorType, Module, register_module

# The tools economy uses to fix a reference, by the host component it lives in.
_FIX_TOOLS = {
    "nodes": ("read_node", "edit_node", "write_node", "read_component"),
    "places": ("read_place", "edit_place", "add_interactable", "set_places_meta", "read_component"),
}


class Economy(Module):
    id = "economy"
    priority = 70   # after the content modules have authored the beats it inspects

    def affected_components(self) -> Tuple[str, ...]:
        return ("nodes", "places")

    def get_errors(self, context) -> List[Error]:
        return [
            Error(type=ErrorType.FIX, code="undeclared_state", component=ref["component"],
                  message=ref["message"], ref=ref["ref"])
            for ref in checks.economy_undeclared(context.artifact)
        ]

    def get_correction_prompt(self, context, error: Error) -> CorrectionPrompt:
        tools = _FIX_TOOLS.get(error.component, ("read_component",))
        rd = render_dict(context, active=error.component, target=error,
                         upstream_views=getattr(context, "upstream_views", {}),
                         available_tools=tools)
        system = load_prompt("fix_economy.txt")
        user = "\n".join(
            cr.spec_block(rd) + [""] + cr.todo_block(rd.get("todo", []))
            + cr.target_block(rd) + cr.upstream_block(rd.get("upstream") or {})
            + cr.tail_block(rd) + ["", "Make the one edit that declares the missing state, or drop "
                                  "the requires. Tool call only."])
        return CorrectionPrompt(system=system, user=user, allowed_tools=tools)


MODULE = Economy()
register_module(MODULE)
