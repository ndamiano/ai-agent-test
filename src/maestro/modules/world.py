"""world — clickable rooms/screens. Authors the `places` component.

A point-and-click world: places connected by `move`, hotspots that examine / take / use / talk,
items that are taken and used, and an optional win goal. A slot-guarded sub-loop grows the map one
room at a time, wiring each new place into the reachable graph.

Example games:
  - "escape a locked observatory before dawn"            — cast + world + inventory
  - "wander a night market solving each vendor's problem" — cast + world + scenes
"""

from typing import Dict, List, Optional

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
    '// background must exist in asset_manifest.backgrounds; a take/requires item id must be\n'
    '//   declared in the `items` catalogue (compose `inventory`); a talk node must exist in\n'
    '//   `nodes` (compose `scenes`); move/win targets resolve.\n'
    '// goal is OPTIONAL: if you want a win, declare a goal flag set by a reachable use-outcome\n'
    '//   and give some hotspot a win action; omit it for an open-ended world.'
)

_PLACE_MODE_TOOLS = frozenset({"write_component", "write_place", "edit_place", "add_interactable",
                               "read_place", "set_places_meta", "read_component", "validate",
                               "update_scratchpad", "request_review"})
_PLACE_TARGET_JOBS = {
    "min_places": "author", "each_place_min_interactables": "author",
    "places_reachable": "fix",
    "crossref": "fix", "compiles": "fix",
}
_PLACE_TARGET_TOOLS = {
    "min_places": frozenset({"write_component", "write_place"}),
    "each_place_min_interactables": frozenset({"read_place", "add_interactable", "edit_place"}),
    "places_reachable": frozenset({"read_place", "add_interactable", "edit_place", "read_component"}),
    # crossref/compiles are the terminal backstop over the WHOLE IR — a dangling reference can be a
    # talk-node (needs write_node/edit_node) as well as a place wiring error, so the fixer gets the
    # broad set rather than routing per-slice.
    "crossref": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                           "write_place", "read_component", "write_node", "edit_node", "read_node"}),
    "compiles": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                           "write_place", "read_component", "write_node", "edit_node", "read_node"}),
}
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


class World(Module):
    id = "world"
    description = ("Clickable rooms/screens you move between — a point-and-click world with "
                   "hotspots, items, and movement. Pair with `scenes` for talkable NPCs.")
    priority = 50
    component = "places"
    mode_prompt = "places_write.txt"
    mode_tools = _PLACE_MODE_TOOLS
    skeleton = SKEL_PLACES
    schemas = {"places": v_places}
    skeletons = {"places": SKEL_PLACES}
    prompts = {"author": "places_write.txt", "fix": "places_fix.txt"}
    target_jobs = _PLACE_TARGET_JOBS
    target_tools = _PLACE_TARGET_TOOLS
    projector = staticmethod(views.place_view)
    projected = True
    emits_compile = True   # a realization terminal: `places` stays writable to the end
    tool_names = ("write_place", "edit_place", "add_interactable", "read_place", "set_places_meta")

    def get_fix(self, context, error: Error):
        # Reaching the place count means ADDING rooms — drive the slot-guarded author loop. Every
        # other place error (reachability, items) is a single edit.
        if error.code == "min_places":
            return partial(author_loop, context, error, module=self, guard=_PLACE_GUARD)
        return super().get_fix(context, error)

    def params(self) -> Dict:
        return {"min_places": 3, "min_interactables": 2}

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
        if not errs:
            for rec in checks.crossref_failures(art):
                errs.append(Error(type=ErrorType.FIX, code="crossref", component="places",
                                  message=rec["message"], path=rec.get("path"), ref=rec.get("ref")))
            if not errs:
                ce = checks.as_error(checks.compile_failure(context.run_dir, context.engine),
                                     type=ErrorType.FIX, code="compiles", component="places")
                if ce:
                    errs.append(ce)
        return errs

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


MODULE = World()
register_module(MODULE)
