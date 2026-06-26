"""navigation — clickable rooms/screens. Authors `places`.

The spine when present: `places` stitches the whole script (the compile terminal), its talk-hotspots
call dialogue_npc nodes, and it carries the place tools. A `move` graph connects places; items are
taken and used; an optional win goal (composed via goal_flag) must be reachable.
"""

from typing import Dict, List, Optional, Tuple

from functools import partial

from maestro import context_render as cr
from maestro.modules import checks, views
from maestro.modules.module import Error, ErrorType, Module, register_module
from maestro.services import author_loop


def v_places(c: Dict) -> Optional[str]:
    place_ids = c.get("place_ids")
    places = c.get("places")
    if not isinstance(place_ids, list) or not place_ids:
        return "places.place_ids must be a non-empty list of place id strings"
    if not isinstance(places, dict):
        return "places.places must be an object mapping place_id -> {background, interactables}"
    if c.get("start_place") and c["start_place"] not in place_ids:
        return f"places.start_place {c['start_place']!r} is not in place_ids"
    if not isinstance(c.get("items", []), list):
        return "places.items must be a list (use [] if none)"
    for i, it in enumerate(c.get("items", [])):
        if not isinstance(it, dict) or not it.get("id"):
            return f"places.items[{i}] needs an 'id' (e.g. 'item_key')"
    goal = c.get("goal")
    if goal is not None and (not isinstance(goal, dict) or not goal.get("type") or not goal.get("id")):
        return "places.goal must be an object with 'type' ('flag' or 'room') and 'id'"
    for pid, place in places.items():
        if not isinstance(place, dict):
            return f"places.places[{pid!r}] must be an object"
        inter = place.get("interactables")
        if not isinstance(inter, list) or not inter:
            return f"places.places[{pid!r}].interactables must be a non-empty list"
        for j, h in enumerate(inter):
            if not isinstance(h, dict) or not h.get("id"):
                return f"places.places[{pid!r}].interactables[{j}] needs an 'id'"
            act = h.get("action")
            if not isinstance(act, dict) or not act.get("type"):
                return f"places.places[{pid!r}].interactables[{j}].action needs a 'type'"
    return None


SKEL_PLACES = (
    '{\n'
    '  "start_place": "room_<first>",\n'
    '  "items": [ {"id": "item_<thing>", "name": "<Display Name>", "examine": "..."} ],\n'
    '  "flags": ["<flag_set_by_a_puzzle>"],\n'
    '  "goal": {"type": "flag", "id": "<flag_that_means_you_won>"},\n'
    '  "place_ids": ["room_<first>", "room_<second>"],\n'
    '  "places": {\n'
    '    "room_<first>": {\n'
    '      "kind": "room",\n'
    '      "background": "bg_<place>",\n'
    '      "interactables": [\n'
    '        {"id": "h_<thing>", "label": "<short noun>",\n'
    '         "position": {"rect": {"x": 340, "y": 210, "w": 180, "h": 160}},\n'
    '         "action": {"type": "examine", "text": "It is a heavy locked door."}}\n'
    '      ]\n'
    '    }\n'
    '  }\n'
    '}\n'
    '// A PLACE is a screen the player clicks. position.rect is pixels on a 1280x720 frame.\n'
    '// action.type is one of (you write the structured action, not Ren\'Py):\n'
    '//   examine {text}; take {item, text?}; talk {node}; move {target, requires?};\n'
    '//   use {clauses:[{requires, outcome:{text?,effects?}}], fallback?}; win {requires?}.\n'
    '//   A `use` clause\'s requires MUST be a real condition (flag/item/var) — NEVER {}. For an\n'
    '//   effect that fires UNCONDITIONALLY (e.g. a lever that always sets a flag), use a\n'
    '//   fallback ONLY and no clauses: {"type":"use","fallback":{"text":"...","effects":[...]}}.\n'
    '// background must exist in asset_manifest.backgrounds; item ids must be declared in\n'
    '//   items; a talk node must exist in `nodes`; move/win targets resolve to places/win.\n'
    '// goal flag must be set by a reachable use-outcome, and some hotspot must have a win action.'
)

_PLACE_MODE_TOOLS = frozenset({"write_component", "write_place", "edit_place", "add_interactable",
                               "read_place", "set_places_meta", "read_component", "validate",
                               "update_scratchpad", "request_review"})
_PLACE_TARGET_JOBS = {
    "min_places": "author", "each_place_min_interactables": "author",
    "items_obtainable": "author", "items_used": "author",
    "places_reachable": "fix",
    "crossref": "fix", "compiles": "fix",
}
_PLACE_TARGET_TOOLS = {
    "min_places": frozenset({"write_component", "write_place"}),
    "each_place_min_interactables": frozenset({"read_place", "add_interactable", "edit_place"}),
    "items_obtainable": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta"}),
    "items_used": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta"}),
    "places_reachable": frozenset({"read_place", "add_interactable", "edit_place", "read_component"}),
    # crossref/compiles are the terminal backstop over the WHOLE IR — a dangling reference can be a
    # talk-node (needs write_node/edit_node) as well as a place wiring error, so the fixer gets the
    # broad set rather than routing per-slice (the old ir_slices map).
    "crossref": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                           "write_place", "read_component", "write_node", "edit_node", "read_node"}),
    "compiles": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                           "write_place", "read_component", "write_node", "edit_node", "read_node"}),
}
# The slot guard for the author loop — the params that turn write_place into "add ONE new place".
_PLACE_GUARD = {"count_tool": "write_place", "id_key": "place_id", "id_list_key": "place_ids",
                "noun": "place"}


def _place_view_block(view: Dict) -> List[str]:
    edges = view.get("edges", {})
    counts = view.get("interactable_counts", {})
    unreachable = set(view.get("unreachable", []))
    place_lines = [
        f"  {pid} -> {edges.get(pid, [])}"
        f"  ({'UNREACHABLE' if pid in unreachable else 'reachable'}, "
        f"{counts.get(pid, 0)} interactables)"
        for pid in view["place_ids"]
    ]
    extra = []
    if view.get("items_never_taken"):
        extra.append(f"items never taken: {view['items_never_taken']}")
    if view.get("items_never_used"):
        extra.append(f"items never used: {view['items_never_used']}")
    return [
        "",
        "CURRENT PLACES (these already exist — reuse these EXACT ids; `move` ONLY to a place "
        "id listed here or one you also create this step):",
        *place_lines,
        *(["  " + " | ".join(extra)] if extra else []),
    ]


class Navigation(Module):
    id = "navigation"
    priority = 50
    component = "places"
    mode_prompt = "write_place.txt"
    mode_tools = _PLACE_MODE_TOOLS
    skeleton = SKEL_PLACES
    schemas = {"places": v_places}
    skeletons = {"places": SKEL_PLACES}
    prompts = {"author": "write_place.txt", "fix": "fix_place.txt"}
    target_jobs = _PLACE_TARGET_JOBS
    target_tools = _PLACE_TARGET_TOOLS
    projector = staticmethod(views.place_view)
    projected = True
    emits_compile = True
    tool_names = ("write_place", "edit_place", "add_interactable", "read_place", "set_places_meta")

    def get_fix(self, context, error: Error):
        # Reaching the place count means ADDING rooms — drive the slot-guarded author loop. Every
        # other place error (reachability, items, the win) is a single edit.
        if error.code == "min_places":
            return partial(author_loop, context, error, module=self, guard=_PLACE_GUARD)
        return super().get_fix(context, error)

    def params(self) -> Dict:
        return {"min_places": 3, "min_interactables": 2}

    def affected_components(self) -> Tuple[str, ...]:
        return ("places",)

    def _add(self, errs, result, code):
        tier = ErrorType.BUILD if self.job_for(code) == "author" else ErrorType.FIX
        e = checks.as_error(result, type=tier, code=code, component="places")
        if e:
            errs.append(e)

    def get_errors(self, context) -> List[Error]:
        art = context.artifact
        errs: List[Error] = []
        self._add(errs, checks.exists(art, "places.start_place"), "start_place")
        self._add(errs, checks.count(art, "places.place_ids", min=context.param("min_places", 3)),
                  "min_places")
        self._add(errs, checks.each_place_min_interactables(
            art, min=context.param("min_interactables", 2)), "each_place_min_interactables")
        self._add(errs, checks.places_reachable(art), "places_reachable")
        self._add(errs, checks.items_obtainable(art), "items_obtainable")
        self._add(errs, checks.items_used(art), "items_used")
        if not errs:
            for rec in checks.crossref_failures(art, context.spec):
                errs.append(Error(type=ErrorType.FIX, code="crossref", component="places",
                                  message=rec["message"], path=rec.get("path"), ref=rec.get("ref")))
            if not errs:
                ce = checks.as_error(checks.compile_failure(context.run_dir, context.engine),
                                     type=ErrorType.FIX, code="compiles", component="places")
                if ce:
                    errs.append(ce)
        return errs

    # start_place / start_place exists is BUILD; classify the two unlisted codes here.
    def job_for(self, code: str) -> str:
        if code == "start_place":
            return "author"
        return super().job_for(code)

    def render_context(self, ctx: Dict) -> str:
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += cr.scratchpad_block(ctx)
        lines += cr.upstream_block(ctx.get("upstream") or {})
        view = ctx.get("active_view") or {}
        if view.get("place_ids"):
            lines += _place_view_block(view)
        lines += cr.story_state_block(ctx)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)

    def render_progress(self, view: Dict) -> str:
        ids = view.get("place_ids")
        if not ids:
            return ""
        note = [f"CURRENT PLACES: {', '.join(ids)}"]
        if view.get("unreachable"):
            note.append(f"UNREACHABLE: {view['unreachable']}")
        return "\n".join(note)


MODULE = Navigation()
register_module(MODULE)
