"""state — the wiring invariant for declared values: flags, variables, and inventory holdings.

Always composed, authors nothing. It enforces one rule over whatever state the other modules
introduce: every declared/used value has BOTH a producer (a way it is set / added / taken) and a
consumer (a gate or use). A missing producer is a dangling reference; a missing consumer is dead
state — use it or cut it. Detects AND fixes, by editing the host the value lives in (a node, a
place, or the item catalog). It does not know items-from-flags-from-vars; it walks the shared
effect/condition grammar uniformly.

Example games:
  - "an escape room whose levers, codes, and keys all matter" — cast + world + inventory + state
  - "a branching romance that remembers what you chose"        — cast + story + scenes + state
"""

from typing import Tuple

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.context import render_dict
from maestro.modules.module import (Check, CorrectionPrompt, Error, Module, load_prompt,
                                     register_module)

# The tools state uses to wire (or cut) a value, by the host component it lives in.
_FIX_TOOLS = {
    "nodes": ("read_node", "edit_node", "write_node", "read_component"),
    "places": ("read_place", "edit_place", "add_interactable", "set_places_meta", "read_component"),
    "items": ("write_component", "read_component"),
}
# A value's PRODUCER and CONSUMER can live in different hosts — a flag consumed by a place gate is
# naturally SET by a narrative node choice (set_flag effect). So the wiring fix offers every host's
# tools, not just the consumer component's; otherwise the prompt tells the model to "edit a node"
# while only place tools are in scope, and it thrashes on the wrong tool.
_WIRING_TOOLS = tuple(sorted(set(t for tools in _FIX_TOOLS.values() for t in tools)))


def _d_state_wiring(chk, m, ctx):
    # The failure lands on the host the value lives in (a node/place/items), not one fixed component.
    return [Error(type=chk.tier, code=chk.code, component=rec["component"],
                  message=rec["message"], ref=rec["ref"])
            for rec in checks.state_wiring(ctx.artifact)]


def _wiring_prompt(m, context, error: Error) -> CorrectionPrompt:
    rd = render_dict(context, active=error.component, target=error,
                     upstream_views=getattr(context, "upstream_views", {}),
                     available_tools=_WIRING_TOOLS)
    system = load_prompt("state_fix.txt")
    user = "\n".join(
        cr.spec_block(rd) + [""] + cr.todo_block(rd.get("todo", []))
        + cr.target_block(rd) + cr.upstream_block(rd.get("upstream") or {})
        + cr.tail_block(rd) + ["", "Make the one edit that wires the value (give it the missing "
                              "producer or consumer), or cut it. Tool call only."])
    return CorrectionPrompt(system=system, user=user, allowed_tools=_WIRING_TOOLS)


class State(Module):
    id = "state"
    selectable = False   # always-on: the wiring invariant holds for every game
    priority = 70        # after the content modules have authored the values it inspects

    checks = [Check("state_wiring", _d_state_wiring, job="fix", build_prompt=_wiring_prompt)]

    def affected_components(self) -> Tuple[str, ...]:
        return ("nodes", "places", "items")


MODULE = State()
register_module(MODULE)
