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

from typing import Dict, Tuple

from maestro import context_render as cr
from maestro.modules import checks, views
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


# ── the wiring walk: gather every value's producers / consumers / declarations ──
def _scan_effects(effs, host: str, rec) -> None:
    for eff in effs or []:
        if not isinstance(eff, dict):
            continue
        for k in ("set_flag", "clear_flag"):
            if eff.get(k):
                rec(eff[k], "flag", "prod", host)
        for k in ("set_var", "add_var"):
            sv = eff.get(k)
            if isinstance(sv, dict):
                rec(sv.get("var"), "variable", "prod", host)
        if eff.get("add_item"):
            rec(eff["add_item"], "item", "prod", host)
        if eff.get("remove_item"):
            rec(eff["remove_item"], "item", "cons", host)


def _scan_condition(cond, host: str, rec) -> None:
    for ref in checks.cond_state_refs(cond):         # flags + variables read by a gate
        rec(ref, None, "cons", host)
    for it in views.cond_items(cond):                # items a clause requires
        rec(it, "item", "cons", host)


def _walk_state(artifact: Dict) -> Dict:
    """For every flag / variable / item id, gather the host components that PRODUCE it (set/add/take),
    CONSUME it (gate/use), and DECLARE it (an explicit catalog/meta entry)."""
    info: Dict = {}

    def rec(sid, kind, slot: str, host: str) -> None:
        if not sid or not isinstance(sid, str):
            return
        e = info.setdefault(sid, {"kind": kind or "state", "prod": set(), "cons": set(), "decl": set()})
        if kind:
            e["kind"] = kind
        e[slot].add(host)

    _, nodes = views.nodes_of(artifact)
    for n in nodes.values():
        _scan_effects(checks.node_effects(n), "nodes", rec)
        end = n.get("end", {}) or {}
        if end.get("type") == "menu":
            for ch in end.get("choices", []) or []:
                _scan_condition(ch.get("requires"), "nodes", rec)

    pc = artifact.get("places") or {}
    for f in (pc.get("flags") or []):
        rec(f, "flag", "decl", "places")
    for v in (pc.get("variables") or []):
        rec(v.get("id") if isinstance(v, dict) else v, "variable", "decl", "places")
    goal = pc.get("goal")
    if isinstance(goal, dict) and goal.get("type") == "flag":
        rec(goal.get("id"), "flag", "cons", "places")        # the win condition reads it
    for p in (pc.get("places") or {}).values():
        for h in p.get("interactables", []):
            a = h.get("action", {}) or {}
            if a.get("type") == "take" and a.get("item"):
                rec(a["item"], "item", "prod", "places")
            _scan_effects(views.action_effects(a), "places", rec)
            for cond in views.action_conditions(a):
                _scan_condition(cond, "places", rec)

    for it in (artifact.get("items") or {}).get("items", []) or []:
        if isinstance(it, dict):
            rec(it.get("id"), "item", "decl", "items")

    return info


def _pick_host(*slots: set) -> str:
    union = set().union(*slots)
    return sorted(union)[0] if union else "nodes"


def state_wiring(artifact: Dict) -> list:
    """Every declared/used scalar or item must have BOTH a producer (a way it's set/added/taken) and
    a consumer (a gate/use). Returns [{component, ref, message}] — a missing producer is a dangling
    reference; a missing consumer is dead state to use or cut. Both are fixed on a host component."""
    out: list = []
    for sid, e in sorted(_walk_state(artifact).items()):
        kind = e["kind"]
        if not e["prod"] and not e["cons"]:
            # A bare declaration nothing touches: two errors here (add a producer / add a consumer)
            # would race each other — one fix wires it while the other cuts it. One verdict: cut it.
            out.append({"component": _pick_host(e["decl"]), "ref": sid, "message": (
                f"{kind} '{sid}' is declared but never produced or consumed — it does nothing. "
                f"Cut the declaration, or wire it fully (an effect that sets it AND a gate that "
                f"reads it).")})
            continue
        if not e["prod"]:
            out.append({"component": _pick_host(e["cons"], e["decl"]), "ref": sid, "message": (
                f"{kind} '{sid}' is read or declared but nothing ever produces it — set/add/take it "
                f"where it should change (an effect or a take hotspot), or drop the reference.")})
        if not e["cons"]:
            out.append({"component": _pick_host(e["prod"], e["decl"]), "ref": sid, "message": (
                f"{kind} '{sid}' is produced or declared but never used — gate a choice/hotspot on it, "
                f"or cut it. Use it or cut it.")})
    return out


def _d_state_wiring(chk, m, ctx):
    # The failure lands on the host the value lives in (a node/place/items), not one fixed component.
    return [Error(type=chk.tier, code=chk.code, component=rec["component"],
                  message=rec["message"], ref=rec["ref"])
            for rec in state_wiring(ctx.artifact)]


def _wiring_report(context) -> list:
    """Every still-unwired value, so ONE fix sees the whole field: wiring value A by re-gating the
    thing that wires value B just moves the hole (the live-build oscillation)."""
    recs = state_wiring(context.artifact)
    if not recs:
        return []
    return ["", "WIRING REPORT — every value still missing a producer or consumer (existing gates "
            "and effects belong to OTHER values on this list; never replace them):"] + [
        f"  - {r['ref']}: {r['message']}" for r in recs]


def _catalog_block(context, error: Error) -> list:
    """The items catalog the fix must edit, shown as the full JSON to copy from — a model told to
    'cut one item' once wrote the list empty because it couldn't see the others."""
    if error.component != "items":
        return []
    import json
    items = [i for i in (context.artifact.get("items") or {}).get("items") or []
             if isinstance(i, dict)]
    return ["", "CURRENT CATALOG — items.items is this list of OBJECTS:",
            json.dumps({"items": items}, ensure_ascii=False, indent=1),
            "To CUT the target item: call write_component(\"items\", <the JSON above minus the "
            "one cut object>) — every kept entry stays a {id, name, ...} OBJECT copied verbatim, "
            "never a bare id string, never an empty list."]


def _wiring_prompt(m, context, error: Error) -> CorrectionPrompt:
    from maestro.modules import inventory, scenes, world
    rd = render_dict(context, active=error.component, target=error,
                     available_tools=_WIRING_TOOLS)
    system = load_prompt("state_fix.txt")
    art = context.artifact
    user = "\n".join(
        cr.spec_block(rd) + [""] + cr.todo_block(rd.get("todo", []))
        + cr.target_block(rd) + _wiring_report(context) + _catalog_block(context, error)
        + inventory.items_block(art) + scenes.nodes_index_block(art)
        + world.places_index_block(art)
        + cr.tail_block(rd) + ["", "Make the one edit that wires the TARGET value (give it the "
                              "missing producer or consumer) WITHOUT touching another value's "
                              "wiring, or cut it. Tool call only."])
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
