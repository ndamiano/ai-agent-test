"""The new module core: checks, per-module get_errors, params resolution, composition + human,
error prioritization, waivers, and the agent loop driving to completion."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.modules import checks, compose
from maestro.modules.context import Context, build_context
from maestro.modules.module import (Check, CorrectionPrompt, Error, ErrorType, MODULE_REGISTRY,
                                     idkey, register_module, Module)
from maestro.agent_loop import AgentLoop, effective_pairs, prioritize
from maestro.modules import human
from tools.spec_tools import _resolve_params


# ── checks ───────────────────────────────────────────────────────────────────
def test_count_check_min():
    art = {"characters": {"characters": [{"id": "a"}]}}
    ok, _ = checks.count(art, "characters.characters", min=2)
    assert ok is False
    ok, _ = checks.count(art, "characters.characters", min=1)
    assert ok is True


def test_reachable_from_start():
    from maestro.modules.scenes import reachable_from_start
    art = {"nodes": {"node_ids": ["a", "b"], "nodes": {
        "a": {"end": {"type": "jump", "target": "b"}}, "b": {"end": {"type": "end"}}}}}
    assert reachable_from_start(art)[0] is True
    art["nodes"]["nodes"]["a"]["end"] = {"type": "end"}  # b now orphaned
    assert reachable_from_start(art)[0] is False


def test_as_error_wraps_failure_only():
    assert checks.as_error((True, None), type=ErrorType.BUILD, code="x", component="c") is None
    e = checks.as_error((False, "bad"), type=ErrorType.FIX, code="x", component="c")
    assert e.type is ErrorType.FIX and e.message == "bad"


# ── params resolution (floors + proposer raise) ──────────────────────────────
def test_params_union_and_raise():
    vn = _resolve_params(["cast", "story", "scenes"], {"each_node_min_lines": 9})
    assert vn["min_characters"] == 2          # story raises cast's floor of 1
    assert vn["each_node_min_lines"] == 9     # proposer raise above floor 6
    assert "voice" in vn["character_fields"]  # list floors UNION
    # proposer may not lower below floor
    assert _resolve_params(["cast", "story", "scenes"],
                           {"each_node_min_lines": 2})["each_node_min_lines"] == 6


# ── cast get_errors reads spec.params ────────────────────────────────────────
def _ctx(spec, art):
    class S:
        run_dir = "/tmp/none"
        def load_artifact(self): return art
        def read_story_state(self): return {}
        def read_waivers(self): return []
        def read_human_todos(self): return []
    return Context(spec=spec, state=S(), artifact=art)


def test_cast_roster_drives_creates():
    cast = MODULE_REGISTRY["cast"]
    # ROSTER present: one create per rostered person not yet carded (the cast is DERIVED, not invented)
    spec = {"characters": [{"id": "dele", "name": "Dele"}, {"id": "marcus", "name": "Marcus"}]}
    art = {"characters": {"characters": [{"id": "dele", "name": "Dele"}]}}
    errs = [e for e in cast.get_errors(_ctx(spec, art)) if e.code == "cast_roster"]
    assert {e.ref for e in errs} == {"marcus"}   # dele carded, only marcus still owed
    # FALLBACK (no roster, older specs): the min_characters count floor
    art1 = {"characters": {"characters": [{"id": "a", "name": "A"}]}}
    assert not any(e.code == "cast_roster" for e in cast.get_errors(_ctx({"params": {}}, art1)))
    errs2 = cast.get_errors(_ctx({"params": {"min_characters": 2}}, art1))
    assert any(e.code == "cast_roster" and e.type is ErrorType.BUILD for e in errs2)


# ── decomposed authoring: cast / story / items grown one item per step ────────
def test_cast_creates_fan_per_roster_id_or_per_slot():
    cast = MODULE_REGISTRY["cast"]
    # fallback (no roster): the count fans into anonymous per-slot creates
    slots = [e for e in cast.get_errors(_ctx({"params": {"min_characters": 3}}, {}))
             if e.code == "cast_roster"]
    assert len(slots) == 3 and {e.path for e in slots} == {"#001", "#002", "#003"}
    guard = cast._check_for("cast_roster").guard
    assert guard["count_tool"] == "add_character" and guard["id_list_key"] == "character_ids"
    # roster: fans one create per uncarded rostered id (path IS the id, not an anonymous slot)
    spec = {"characters": [{"id": "x"}, {"id": "y"}, {"id": "z"}]}
    assert {e.path for e in cast.get_errors(_ctx(spec, {})) if e.code == "cast_roster"} == {"x", "y", "z"}
    # authoring one shrinks the owed set (visible progress, stable identity)
    art = {"characters": {"characters": [{"id": "x", "name": "X"}]}}
    assert {e.path for e in cast.get_errors(_ctx(spec, art)) if e.code == "cast_roster"} == {"y", "z"}


def test_story_spine_blocks_then_storylines_and_beats_fan():
    story = MODULE_REGISTRY["story"]
    p = {"params": story.params()}
    # no story -> ONLY the blocking spine error (spine before the rest)
    assert [e.code for e in story.get_errors(_ctx(p, {}))] == ["spine"]
    # spine set, no storylines yet -> the main-storyline bootstrap error
    spine_only = {"story": {"spine": {"theme": "corruption", "tone": "tense"},
                            "start_storyline": "sl_main", "storylines": []}}
    assert [e.code for e in story.get_errors(_ctx(p, spine_only))] == ["main_storyline"]
    # a main storyline with target_beats added -> storyline_beats fans one error per beat owed
    art = {"story": {"spine": {"theme": "corruption", "tone": "tense"},
                     "start_storyline": "sl_main",
                     "storylines": [{"id": "sl_main", "kind": "main", "premise": "p",
                                     "target_beats": 4, "beats": [],
                                     "terminus": {"type": "game_end",
                                                  "ending": {"id": "e1", "description": "d"}}}]}}
    errs = story.get_errors(_ctx(p, art))
    assert len([e for e in errs if e.code == "storyline_beats"]) == 4
    # beats are authored ONE at a time (each sees its arc so far), not batched in parallel
    assert story._check_for("storyline_beats").guard["cap"](None) == 1


def test_items_are_demand_driven_no_floor():
    from maestro.modules import inventory
    inv = MODULE_REGISTRY["inventory"]
    # nothing references an item -> no work, no floor (empty or absent catalog is clean)
    assert inv.get_errors(_ctx({"params": {}}, {})) == []
    assert inv.get_errors(_ctx({"params": {}}, {"items": {"items": []}})) == []
    # a node add_item effect DEMANDS that exact item
    art = {"items": {"items": []}, "nodes": {"node_ids": ["n1"], "nodes": {"n1": {
        "lines": [{"speaker": "a", "text": "x", "effects": [{"add_item": "item_key"}]}],
        "end": {"type": "end"}}}}}
    assert inventory.demanded_items(art) == ["item_key"]
    demanded = [e for e in inv.get_errors(_ctx({"params": {}}, art)) if e.code == "demanded_items"]
    assert len(demanded) == 1 and demanded[0].ref == "item_key"
    # once declared, the demand clears
    art["items"]["items"].append({"id": "item_key", "name": "Key", "examine": "a key"})
    assert inventory.demanded_items(art) == []


def test_scratchpad_tool_is_gone():
    from maestro.tools import TOOL_SCHEMAS
    assert not any(s["function"]["name"] == "update_scratchpad" for s in TOOL_SCHEMAS)


def test_state_wiring_demands_producer_and_consumer():
    # A flag set but never read (orphan) and a flag read but never set (dangling) both fail.
    art = {"nodes": {"node_ids": ["n1", "n2"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "x", "effects": [{"set_flag": "orphan"}]}],
               "end": {"type": "jump", "target": "n2"}},
        "n2": {"lines": [{"speaker": "a", "text": "y"}], "end": {"type": "menu", "choices": [
            {"text": "go", "target": "n1", "requires": {"flag": "never_set"}},
            {"text": "stay", "target": "n1"}]}}}}}
    from maestro.modules.state import state_wiring
    refs = {r["ref"] for r in state_wiring(art)}
    assert "orphan" in refs        # produced, never consumed -> use it or cut it
    assert "never_set" in refs     # consumed, never produced -> dangling

    # A flag both set and gated is fully wired -> no error.
    ok = {"nodes": {"node_ids": ["n1", "n2"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "x", "effects": [{"set_flag": "f"}]}],
               "end": {"type": "jump", "target": "n2"}},
        "n2": {"lines": [{"speaker": "a", "text": "y"}], "end": {"type": "menu", "choices": [
            {"text": "go", "target": "n1", "requires": {"flag": "f"}},
            {"text": "stay", "target": "n1"}]}}}}}
    assert state_wiring(ok) == []


def test_state_wiring_survives_model_shaped_junk_conditions():
    # A model may emit "requires": {"item": [list]} or {"flag": {...}} — detectors must report,
    # never crash (this killed a live build at step 101).
    art = {"nodes": {"node_ids": ["n1"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "x"}], "end": {"type": "menu", "choices": [
            {"text": "a", "target": "n1", "requires": {"item": ["item_key", "item_ash"]}},
            {"text": "b", "target": "n1", "requires": {"flag": {"nested": "junk"}}}]}}}}}
    from maestro.modules.state import state_wiring
    refs = {r["ref"] for r in state_wiring(art)}
    assert {"item_key", "item_ash"} <= refs      # list items still counted as consumers
    from maestro.modules import views
    assert views.cond_items({"item": 42}) == set()


def test_state_wiring_fully_orphaned_value_gets_one_error():
    # A declared flag nothing produces AND nothing consumes: one "cut it" error, not a
    # missing-producer error and a missing-consumer error that would fight each other.
    art = {"places": {"place_ids": [], "places": {}, "flags": ["untouched"]}}
    from maestro.modules.state import state_wiring
    errs = [r for r in state_wiring(art) if r["ref"] == "untouched"]
    assert len(errs) == 1
    assert "never produced or consumed" in errs[0]["message"]


def test_state_is_forced_and_detects_via_get_errors():
    state = MODULE_REGISTRY["state"]
    assert state.selectable is False
    art = {"nodes": {"node_ids": ["n1"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "x", "effects": [{"set_flag": "lonely"}]}],
               "end": {"type": "return"}}}}}
    errs = state.get_errors(_ctx({"params": {}}, art))
    assert errs and all(e.type is ErrorType.FIX and e.component == "nodes" for e in errs)


def test_inventory_catalog_checks():
    inv = MODULE_REGISTRY["inventory"]
    bad = {"items": {"items": [{"name": "Key"}]}}        # missing id
    assert any(e.code == "item_fields" for e in inv.get_errors(_ctx({"params": {}}, bad)))
    good = {"items": {"items": [{"id": "item_key", "name": "Key"}]}}
    assert inv.get_errors(_ctx({"params": {}}, good)) == []


def test_scenes_floor_gated_on_story_presence():
    # scenes reads the artifact (not a flag): the narrative floor fires ONLY when a `story`
    # component is present. The same nodes graph, with/without story, gets different bars.
    scenes = MODULE_REGISTRY["scenes"]
    # A dangling jump keeps a structural error open, so get_errors never reaches the compile
    # backstop — we're only inspecting which checks fire, not building.
    nodes = {"nodes": {"node_ids": ["n1"], "nodes": {
        "n1": {"location": "bg", "lines": [{"speaker": "a", "text": "x"}],
               "end": {"type": "jump", "target": "ghost"}}}}}
    story = {"story": {"spine": {"theme": "corruption", "tone": "tense"},
                       "start_storyline": "sl_main",
                       "storylines": [{"id": "sl_main", "kind": "main", "premise": "p",
                                       "target_beats": 1,
                                       "beats": [{"id": "b1", "summary": "s", "type": "plot",
                                                  "purpose": "setup", "tension": "none"}],
                                       "terminus": {"type": "game_end",
                                                    "ending": {"id": "e1", "description": "d"}}}]}}
    params = {"params": {"each_node_min_lines": 1, "min_branches": 1}}

    light = {e.code for e in scenes.get_errors(_ctx(params, dict(nodes)))}
    rich = {e.code for e in scenes.get_errors(_ctx(params, {**nodes, **story}))}

    narrative = {"beats_realized", "min_branches", "all_characters_speak"}
    assert not (narrative & light)        # no story -> no narrative floor
    assert narrative & rich               # story present -> narrative floor fires


def test_storyline_graph_derives_structural_ends():
    # Each beat-node's `end` is DERIVED from the storyline structure, never model-chosen:
    # a jump to the next beat, a menu at a branch point, the game_end terminus (end), and a
    # handoff terminus that jumps back to the branch's encoded return beat.
    from maestro.modules.scenes import _storyline_graph
    story = {"spine": {"theme": "loyalty", "tone": "wry"}, "start_storyline": "sl_main",
             "storylines": [
                 {"id": "sl_main", "kind": "main", "premise": "p", "target_beats": 3,
                  "beats": [{"id": "beat_01", "summary": "s", "type": "plot",
                             "purpose": "setup", "tension": "none"},
                            {"id": "beat_02", "summary": "s", "type": "plot",
                             "purpose": "crisis", "tension": "high"},
                            {"id": "beat_03", "summary": "s", "type": "plot",
                             "purpose": "resolution", "tension": "none"}],
                  "branches": [{"id": "br", "from_beat": "beat_02", "choice": "split up",
                                "spinoff": "sl_side", "return_to_beat": "beat_03",
                                "requires": None}],
                  "terminus": {"type": "game_end",
                               "ending": {"id": "e1", "description": "caught"}}},
                 {"id": "sl_side", "kind": "side", "premise": "p", "target_beats": 1,
                  "beats": [{"id": "beat_10", "summary": "s", "type": "friction",
                             "purpose": "escalation", "tension": "outnumbered"}],
                  "branches": [], "terminus": {"type": "handoff"}}]}
    g = _storyline_graph({"story": story})
    assert g["entry"] == "beat_01"
    assert g["ends"]["beat_01"] == {"type": "jump", "target": "beat_02"}   # linear jump
    branch = g["ends"]["beat_02"]                                          # branch → menu
    assert branch["type"] == "menu"
    assert {c["target"] for c in branch["choices"]} == {"beat_03", "beat_10"}
    assert g["ends"]["beat_03"] == {"type": "end"}                        # game_end terminus
    assert g["ends"]["beat_10"] == {"type": "jump", "target": "beat_03"}  # handoff → return beat


# ── compose always includes human; prioritization ───────────────────────────
def test_compose_includes_human():
    ids = [m.id for m in compose(("cast", "scenes"))]
    assert ids[0] == "human" and "cast" in ids


def test_prioritize_human_then_build_then_fix():
    m = MODULE_REGISTRY["cast"]
    pairs = [(m, Error(ErrorType.FIX, "f", "premise", "f")),
             (m, Error(ErrorType.BUILD, "b", "premise", "b")),
             (m, Error(ErrorType.HUMAN, "h", "premise", "h"))]
    assert prioritize(pairs)[1].type is ErrorType.HUMAN


def test_prioritize_survives_mixed_none_and_str_paths():
    # Two same-code errors, one with path=None and one with a str path, must not TypeError on the
    # identity tiebreak (None < str is unorderable).
    m = MODULE_REGISTRY["scenes"]
    pairs = [(m, Error(ErrorType.FIX, "crossref", "nodes", "a", path=None)),
             (m, Error(ErrorType.FIX, "crossref", "nodes", "b", path="nodes.n1"))]
    assert prioritize(pairs)[1].code == "crossref"


def test_prioritize_follows_check_declaration_order():
    # Within a module, fix order = the checks list's declared order, NOT alphabetical code order:
    # authoring (beats_realized, first check) must outrank polish (all_characters_speak, later),
    # even though "all_characters_speak" sorts first alphabetically.
    scenes = MODULE_REGISTRY["scenes"]
    pairs = [(scenes, Error(ErrorType.BUILD, "all_characters_speak", "nodes", "silent cast")),
             (scenes, Error(ErrorType.BUILD, "beats_realized", "nodes", "one more scene",
                            path="#001"))]
    assert prioritize(pairs)[1].code == "beats_realized"
    assert scenes.check_rank("beats_realized") < scenes.check_rank("all_characters_speak")
    assert scenes.check_rank("no_such_code") == len(scenes.checks)


# ── human todos + waivers ────────────────────────────────────────────────────
def test_human_todo_becomes_top_priority(tmp_path):
    state = RunState(tmp_path)
    human.add_todo(state, "premise", "make the villain meaner")
    h = MODULE_REGISTRY["human"]
    ctx = build_context({"params": {}}, state)
    errs = h.get_errors(ctx)
    assert len(errs) == 1 and errs[0].type is ErrorType.HUMAN


def test_waiver_subtracts_from_effective(tmp_path):
    state = RunState(tmp_path)
    state.write_spec({"frozen": True, "modules": ["cast"], "params": {}})
    spec = state.read_spec()
    modules = compose(spec.get("modules", []))
    ctx = build_context(spec, state)
    pairs = effective_pairs(modules, ctx)
    assert pairs, "cast should have open BUILD errors on an empty premise"
    # waive every current error -> effective empties
    for _, e in pairs:
        human.waive(state, idkey(e))
    ctx2 = build_context(spec, state)
    assert effective_pairs(modules, ctx2) == []


# ── agent loop drives a scripted module to completion ────────────────────────
class _OneShot(Module):
    id = "_oneshot_test"
    priority = 5

    def affected_components(self):
        return ("premise",)

    def get_errors(self, ctx):
        return [] if ctx.artifact.get("premise") else [Error(ErrorType.BUILD, "mk", "premise", "write it")]

    def get_correction_prompt(self, ctx, error, slot=0):
        return CorrectionPrompt("s", "u", ("write_component",))


class _Conn:
    def generate_with_tools(self, messages, schemas, **kw):
        return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "write_component",
            "arguments": '{"component_id": "premise", "content": {"ok": 1}}'}}]}}]}


def test_loop_completes_when_errors_clear(tmp_path):
    state = RunState(tmp_path)
    register_module(_OneShot())
    tools = {"write_component": lambda component_id, content, **kw: (
        state.write_component(component_id, content) or {"ok": True})}
    loop = AgentLoop({"frozen": True}, state, [_OneShot()], tools, connector=_Conn(), max_steps=5)
    result = loop.run()
    assert result.ok is True
    assert state.read_component("premise") == {"ok": 1}


def test_build_started_and_step_events_carry_live_todo(tmp_path):
    """Epic C5: build_started/build_step must carry the effective to-do (the frontend reads
    msg.todo — previously a dead wire, since the backend never set it)."""
    state = RunState(tmp_path)
    register_module(_OneShot())
    tools = {"write_component": lambda component_id, content, **kw: (
        state.write_component(component_id, content) or {"ok": True})}
    events = []
    loop = AgentLoop({"frozen": True}, state, [_OneShot()], tools, connector=_Conn(),
                     max_steps=5, on_event=events.append)
    result = loop.run()
    assert result.ok is True

    started = next(e for e in events if e["type"] == "build_started")
    assert started["todo"] == [{"component": "premise", "code": "mk", "type": "build",
                               "detail": "write it", "idkey": idkey(Error(
                                   ErrorType.BUILD, "mk", "premise", "write it")), "path": None}]

    step = next(e for e in events if e["type"] == "build_step")
    assert step["todo"] and step["todo"][0]["component"] == "premise"
    assert all({"component", "code", "type", "detail", "idkey", "path"} <= set(t) for t in step["todo"])


def test_build_started_and_step_carry_elapsed_timing(tmp_path):
    """Epic E2: build_started carries a wall-clock `started_at`, and every build_step carries
    `elapsed` (seconds since that start) — the frontend's progress header ticks off these."""
    state = RunState(tmp_path)
    register_module(_OneShot())
    tools = {"write_component": lambda component_id, content, **kw: (
        state.write_component(component_id, content) or {"ok": True})}
    events = []
    loop = AgentLoop({"frozen": True}, state, [_OneShot()], tools, connector=_Conn(),
                     max_steps=5, on_event=events.append)
    result = loop.run()
    assert result.ok is True

    started = next(e for e in events if e["type"] == "build_started")
    assert isinstance(started["started_at"], float) and started["started_at"] > 0

    step = next(e for e in events if e["type"] == "build_step")
    assert isinstance(step["elapsed"], float) and step["elapsed"] >= 0.0


# ── count target: fanned to one per-slot create-error, one item authored per step ────
from maestro.modules import checks


def _need_items(chk, m, ctx):
    n = len((ctx.artifact.get("items") or {}).get("ids", []))
    return checks.slot_errors(max(0, 2 - n), type=chk.tier, code=chk.code,
                              component="items", noun="item")


class _Counter(Module):
    """Needs 2 items; a shortfall fans into per-slot create-errors, and the base single fix (with
    the slot guard from its check) adds one item per step."""
    id = "_counter_test"
    component = "items"
    mode_prompt = "nodes_write.txt"     # any existing prompt; content irrelevant to the test
    mode_tools = frozenset({"add"})
    checks = [Check("need_items", _need_items,
                    guard={"count_tool": "add", "id_key": "id", "id_list_key": "ids",
                           "noun": "item"})]

    def view(self, artifact):
        return {"ids": (artifact.get("items") or {}).get("ids", []), "open_slots": None}

    def render_context(self, ctx):
        return "add an item"


class _AddConn:
    """Returns an `add` tool call with a fresh id each call."""
    def __init__(self):
        self.i = 0

    def generate_with_tools(self, messages, schemas, **kw):
        self.i += 1
        return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "add", "arguments": f'{{"id": "item_{self.i}"}}'}}]}}]}


def test_count_target_authors_one_item_per_step_to_green(tmp_path):
    state = RunState(tmp_path)

    def add(id, **kw):
        items = state.read_component("items") or {"ids": []}
        items["ids"].append(id)
        state.write_component("items", items)
        return {"ok": True, "id": id}

    loop = AgentLoop({"frozen": True}, state, [_Counter()], {"add": add},
                     connector=_AddConn(), max_steps=10)
    result = loop.run()
    assert result.ok is True
    assert state.read_component("items")["ids"] == ["item_1", "item_2"]  # stopped at the target, not forever


# ── parallel fixes: same-code slot creates run concurrently, writes stay serial ──
import threading
import time as _time

from maestro.modules.scenes import _stamp_node, pick_slot
from maestro.services import _create_guard


def test_pick_slot_indexed_in_dramatic_order():
    view = {"open_slots": [{"id": "z_late", "beat": "beat_02"}, {"id": "a_early", "beat": "beat_01"}],
            "beats": [{"id": "beat_01"}, {"id": "beat_02"}]}
    assert pick_slot(view, 0)["id"] == "a_early"
    assert pick_slot(view, 1)["id"] == "z_late"
    assert pick_slot(view, 2) is None


def test_create_guard_code_fills_the_assigned_slot_id():
    # WHY: the model never chooses a slot-create's node id — prepare overrides whatever id it
    # picked with the assigned slot's (observed live: ids grabbed from sibling storylines' beats
    # in context wedged those slots for good). The write goes through, never a refusal loop.
    calls = []
    ok = lambda name, args: (calls.append((name, args)), {"ok": True})[1]
    view = {"node_ids": ["n1"], "open_slots": [{"id": "s1"}, {"id": "s2"}], "beats": []}
    g = _create_guard(ok, lambda: view, "write_node", "node_id", "node_ids", "node",
                      assigned={"id": "s2", "beat": "beat_02"}, prepare=_stamp_node)
    res = g("write_node", {"node_id": "s1"})   # model-picked id is discarded, not refused
    assert res["ok"] is True
    assert calls and calls[0][1]["node_id"] == "s2"
    assert calls[0][1]["beat"] == "beat_02"   # system stamps the assigned slot's beat


# ── storyline slot identity: the slot's target id IS the beat it realizes ────
def _beat(i):
    return {"id": f"beat_{i:02d}", "summary": "s", "type": "plot",
            "purpose": "setup", "tension": "none"}


def _branching_story():
    # The live failure's shape: a main line branching at one beat into two terminal spinoffs.
    return {"story": {
        "spine": {"theme": "t", "tone": "n"}, "start_storyline": "sl_main",
        "storylines": [
            {"id": "sl_main", "kind": "main", "premise": "p", "target_beats": 3,
             "beats": [_beat(1), _beat(2), _beat(3)],
             "branches": [
                 {"id": "br_jax", "from_beat": "beat_02", "choice": "jax goes",
                  "spinoff": "sl_jax", "return_to_beat": None},
                 {"id": "br_mara", "from_beat": "beat_02", "choice": "mara goes",
                  "spinoff": "sl_mara", "return_to_beat": None}],
             "terminus": {"type": "game_end", "ending": {"id": "e1", "description": "d"}}},
            {"id": "sl_jax", "kind": "side", "premise": "p", "target_beats": 2,
             "beats": [_beat(10), _beat(11)], "branches": [],
             "terminus": {"type": "game_end", "ending": {"id": "e2", "description": "d"}}},
            {"id": "sl_mara", "kind": "side", "premise": "p", "target_beats": 1,
             "beats": [_beat(13)], "branches": [],
             "terminus": {"type": "game_end", "ending": {"id": "e3", "description": "d"}}}]}}


def _node(beat, end, storyline="sl_main"):
    return {"storyline": storyline, "beat": beat,
            "lines": [{"speaker": "a", "text": "x"}], "end": end}


def _branched_art():
    # beat_02's structural menu fans three open slots: the main continuation + both spinoff roots.
    menu = {"type": "menu", "choices": [{"text": "on", "target": "beat_03"},
                                        {"text": "jax", "target": "beat_10"},
                                        {"text": "mara", "target": "beat_13"}]}
    return {**_branching_story(), "nodes": {
        "node_ids": ["beat_01", "beat_02"],
        "nodes": {"beat_01": _node("beat_01", {"type": "jump", "target": "beat_02"}),
                  "beat_02": _node("beat_02", menu)}}}


def test_branch_slot_realizes_its_own_beat_not_the_parents_successor():
    # WHY: the live park at step 833 — every open slot's beat was derived as "parent's beat + 1
    # in the flat list", so both spinoff roots (beat_10, beat_13) were stamped (sl_main, beat_05)
    # and their real beats stayed "unrealized" forever. The slot's target id IS its beat.
    from maestro.modules.scenes import node_view
    view = node_view(_branched_art())
    by_id = {s["id"]: s for s in view["open_slots"]}
    assert set(by_id) == {"beat_03", "beat_10", "beat_13"}
    assert by_id["beat_03"]["beat"] == "beat_03"
    assert by_id["beat_10"]["beat"] == "beat_10"
    assert by_id["beat_13"]["beat"] == "beat_13"


def test_parallel_slots_stamp_distinct_storyline_beat_pairs():
    # WHY: a batch of parallel fixes must never share a (storyline, beat) — the live build wrote
    # three sibling nodes all stamped (sl_main, beat_05) in one batch.
    from maestro.modules.scenes import node_view
    view = node_view(_branched_art())
    stamps = []
    for i in range(3):
        assigned = pick_slot(view, i)
        args = _stamp_node(view, assigned, {})
        assert args["node_id"] == assigned["id"]
        stamps.append((args["storyline"], args["beat"]))
    assert sorted(stamps) == [("sl_jax", "beat_10"), ("sl_main", "beat_03"),
                              ("sl_mara", "beat_13")]


def test_node_id_realizes_its_beat_even_when_the_stamp_disagrees():
    # WHY: the terminal spin — a node named beat_10 existed with a WRONG stamp, so beat_10 kept
    # fanning a create-error that write_scene could only answer with "already exists". A node
    # whose id names a beat realizes it regardless of its stamp, so the escape hatch can never
    # be pointed at an id that is already taken.
    from maestro.modules.scenes import node_view, unrealized_beats
    art = _branched_art()
    art["nodes"]["node_ids"].append("beat_10")
    art["nodes"]["nodes"]["beat_10"] = _node("beat_03", {"type": "jump", "target": "beat_11"})
    todo = unrealized_beats(art)
    assert "beat_10" not in todo
    view = node_view(art)
    assert "beat_10" not in view["beats_todo"]
    # the escape hatch (no assigned slot) targets an UNWRITTEN beat, never an existing node
    forced = _stamp_node(view, None, {})
    assert forced["node_id"] in view["beats_todo"]
    assert forced["node_id"] not in art["nodes"]["node_ids"]


def test_opening_node_id_is_the_start_storylines_entry_beat():
    # WHY: the opening node's id is code-picked from start_storyline (not list order, not the
    # model) so the structural graph's entry lands on a node whose id matches.
    from maestro.modules.scenes import node_view
    art = _branching_story()
    art["story"]["storylines"].reverse()          # start_storyline is now listed LAST
    view = node_view(art)
    assert view["entry_beat"] == "beat_01"
    args = _stamp_node(view, None, {"node_id": "model_pick"})
    assert args["node_id"] == "beat_01"
    assert args["storyline"] == "sl_main" and args["beat"] == "beat_01"


def _need3(chk, m, ctx):
    n = len((ctx.artifact.get("items") or {}).get("ids", []))
    return checks.slot_errors(max(0, 3 - n), type=chk.tier, code=chk.code,
                              component="items", noun="item")


class _Counter3(Module):
    id = "_counter3_test"
    component = "items"
    mode_prompt = "nodes_write.txt"
    mode_tools = frozenset({"add"})
    checks = [Check("need3", _need3,
                    guard={"count_tool": "add", "id_key": "id", "id_list_key": "ids",
                           "noun": "item"})]

    def view(self, artifact):
        return {"ids": (artifact.get("items") or {}).get("ids", [])}

    def render_context(self, ctx):
        return "add an item"


class _SlowAddConn:
    """Each call sleeps so overlap is observable; records the max in-flight calls."""
    def __init__(self):
        self.i = 0
        self.inflight = 0
        self.max_inflight = 0
        self.lock = threading.Lock()

    def generate_with_tools(self, messages, schemas, **kw):
        with self.lock:
            self.i += 1
            n = self.i
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        _time.sleep(0.05)
        with self.lock:
            self.inflight -= 1
        return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "add", "arguments": f'{{"id": "item_{n}"}}'}}]}}]}


def test_parallel_slot_creates_overlap_llm_calls_and_serialize_writes(tmp_path):
    state = RunState(tmp_path)

    def add(id, **kw):
        # Deliberately non-atomic read-modify-write: the dispatch lock is what keeps it safe.
        items = state.read_component("items") or {"ids": []}
        items["ids"].append(id)
        state.write_component("items", items)
        return {"ok": True, "id": id}

    conn = _SlowAddConn()
    loop = AgentLoop({"frozen": True}, state, [_Counter3()], {"add": add},
                     connector=conn, max_steps=20, parallel=3)
    result = loop.run()
    assert result.ok is True
    assert sorted(state.read_component("items")["ids"]) == ["item_1", "item_2", "item_3"]
    assert conn.max_inflight >= 2      # the LLM calls actually overlapped
    assert conn.i == 3                 # one call per owed slot, no retries lost to write races


def test_batch_is_single_for_unguarded_errors(tmp_path):
    state = RunState(tmp_path)
    loop = AgentLoop({"frozen": True}, state, [_Counter3()], {}, connector=_SlowAddConn(),
                     max_steps=1, parallel=4)
    ctx = build_context({"params": {}}, state)
    m = MODULE_REGISTRY["cast"]
    e = Error(ErrorType.BUILD, "min_characters", "characters", "need more")
    assert loop._batch(ctx, [(m, e)], m, e) == [(m, e, 0)]


def test_batch_capped_by_open_slots(tmp_path):
    # A nodes-style view publishes open_slots: the batch never exceeds the slots that exist,
    # however many creates are owed.
    state = RunState(tmp_path)

    from maestro.modules.scenes import _parallel_cap

    class _Slotted(_Counter3):
        id = "_slotted_test"
        checks = [Check("need3", _need3,
                        guard={"count_tool": "add", "id_key": "id", "id_list_key": "ids",
                               "noun": "item", "cap": _parallel_cap})]

        def view(self, artifact):
            return {"ids": ["n1"], "node_ids": ["n1"], "open_slots": [{"id": "s1"}], "beats": []}

    mod = _Slotted()
    loop = AgentLoop({"frozen": True}, state, [mod], {}, connector=_SlowAddConn(),
                     max_steps=1, parallel=4)
    ctx = build_context({"params": {}}, state)
    errs = checks.slot_errors(3, type=ErrorType.BUILD, code="need3", component="items", noun="item")
    pairs = [(mod, e) for e in errs]
    batch = loop._batch(ctx, pairs, mod, errs[0])
    assert len(batch) == 1


def test_batch_is_single_on_an_empty_graph(tmp_path):
    # No nodes yet: one worker writes the opening node; parallel roots would make a forest.
    state = RunState(tmp_path)

    from maestro.modules.scenes import _parallel_cap

    class _Empty(_Counter3):
        id = "_empty_graph_test"
        checks = [Check("need3", _need3,
                        guard={"count_tool": "add", "id_key": "id", "id_list_key": "ids",
                               "noun": "item", "cap": _parallel_cap})]

        def view(self, artifact):
            return {"ids": [], "node_ids": [], "open_slots": [], "beats": []}

    mod = _Empty()
    loop = AgentLoop({"frozen": True}, state, [mod], {}, connector=_SlowAddConn(),
                     max_steps=1, parallel=4)
    ctx = build_context({"params": {}}, state)
    errs = checks.slot_errors(3, type=ErrorType.BUILD, code="need3", component="items", noun="item")
    batch = loop._batch(ctx, [(mod, e) for e in errs], mod, errs[0])
    assert len(batch) == 1


# ── consumer-crafted context: each module composes its own blocks from the raw artifact ──
_RICH_ART = {
    "characters": {"characters": [{"id": "ana", "name": "Ana", "role": "protagonist",
                                   "voice": "curt", "drive": "keep the farm",
                                   "history": ["lost the farm to the bank"],
                                   "competencies": ["lockpicking"],
                                   "example_lines": ["Hand me the crowbar."]}]},
    "asset_manifest": {"backgrounds": [{"id": "bg_barn", "description": "a collapsing barn"}],
                       "characters": [], "cgs": []},
    "story": {"spine": {"theme": "saving the farm", "tone": "quiet desperation"},
              "start_storyline": "sl_main",
              "storylines": [{"id": "sl_main", "kind": "main",
                              "premise": "Ana fights to keep the family farm",
                              "target_beats": 1,
                              "beats": [{"id": "b1", "summary": "the notice arrives",
                                         "type": "plot", "purpose": "setup", "tension": "none"}],
                              "terminus": {"type": "game_end",
                                           "ending": {"id": "ending_saved",
                                                      "description": "she keeps it"}}}]},
    "items": {"items": [{"id": "item_deed", "name": "Deed", "examine": "the farm's deed"}]},
    "nodes": {"node_ids": ["s1"], "synopses": {"s1": "the reveal"},
              "nodes": {"s1": {"lines": [{"speaker": None, "text": "sekritlongtext " * 200}],
                               "end": {"type": "end"}}}},
}


def test_scene_author_context_is_crafted_and_bounded():
    # The dialogue author gets the FULL character card, locations with descriptions, the story
    # plan, and the item list — but never another scene's full text (that's what overflowed the
    # context window in a live build).
    scenes = MODULE_REGISTRY["scenes"]
    ctx = _ctx({"title": "T", "params": {}}, _RICH_ART)
    err = Error(ErrorType.BUILD, "beats_realized", "nodes", "one more scene", path="#001")
    p = scenes.get_correction_prompt(ctx, err)
    assert "history: lost the farm to the bank" in p.user   # full card, not a trim
    assert "bg_barn — a collapsing barn" in p.user           # location has its description
    assert "STORY — theme: saving the farm | tone: quiet desperation" in p.user
    assert "item_deed" in p.user
    assert "sekritlongtext" not in p.user                    # scene text stays out


def test_world_author_context_is_crafted_and_bounded():
    world = MODULE_REGISTRY["world"]
    ctx = _ctx({"title": "T", "params": {}}, _RICH_ART)
    err = Error(ErrorType.BUILD, "min_places", "places", "one more place", path="#001")
    p = world.get_correction_prompt(ctx, err)
    assert "item_deed — Deed" in p.user       # full catalogue to place takes/gates
    assert 's1 — "the reveal"' in p.user      # scene index for talk targets
    assert "sekritlongtext" not in p.user     # never the scene text


def test_open_slot_carries_parent_lead_in_lines():
    from maestro.modules.scenes import node_view
    art = {"nodes": {"node_ids": ["s1"], "nodes": {
        "s1": {"lines": [{"speaker": "a", "text": f"line {i}"} for i in range(9)],
               "end": {"type": "jump", "target": "s2"}}}}}
    slot = node_view(art)["open_slots"][0]
    assert slot["id"] == "s2"
    assert [l["text"] for l in slot["lead_in"]] == [f"line {i}" for i in range(3, 9)]


# ── nodes must be enterable from the world (talk / encounter resolution) ─────
def test_nodes_world_entered():
    from maestro.modules.world import nodes_world_entered
    places = {"places": {"place_ids": ["p1"], "places": {"p1": {"kind": "room", "interactables": [
        {"id": "h1", "action": {"type": "examine", "text": "t"}}]}}}}
    nodes = {"nodes": {"node_ids": ["s1", "s2"], "nodes": {
        "s1": {"lines": [{"speaker": None, "text": "x"}], "end": {"type": "jump", "target": "s2"}},
        "s2": {"lines": [{"speaker": None, "text": "y"}], "end": {"type": "end"}}}}}
    ok, msg = nodes_world_entered({**places, **nodes})
    assert ok is False and "NEVER entered" in msg
    # a talk hotspot at s1 makes the whole chain reachable
    places["places"]["places"]["p1"]["interactables"].append(
        {"id": "h2", "action": {"type": "talk", "node": "s1"}})
    assert nodes_world_entered({**places, **nodes})[0] is True
    # an on_victory jump also counts as an entry
    combat = {"combat": {"encounters": [{"id": "e1", "combatants": [],
                                         "on_victory": {"type": "jump", "target": "s1"}}]}}
    places["places"]["places"]["p1"]["interactables"].pop()
    assert nodes_world_entered({**places, **nodes, **combat})[0] is True
    # a node OFF the entered graph is called out by id
    nodes["nodes"]["node_ids"].append("s3")
    nodes["nodes"]["nodes"]["s3"] = {"lines": [{"speaker": None, "text": "z"}],
                                     "end": {"type": "end"}}
    ok, msg = nodes_world_entered({**places, **nodes, **combat})
    assert ok is False and "s3" in msg
    # no places -> VN entry rules apply, check is silent
    assert nodes_world_entered(nodes)[0] is True


# ── parked errors: a stuck error stops monopolizing the budget ────────────────
class _Stuck(Module):
    id = "_stuck_test"
    component = "premise"
    mode_prompt = "nodes_write.txt"
    mode_tools = frozenset({"noop"})

    def affected_components(self):
        return ("premise",)

    def get_errors(self, ctx):
        return [Error(ErrorType.BUILD, "unfixable", "premise", "never clears")]

    def get_correction_prompt(self, ctx, error, slot=0):
        return CorrectionPrompt("s", "u", ("noop",))


def test_stuck_error_parks_instead_of_burning_budget(tmp_path):
    state = RunState(tmp_path)
    conn = _AddConn()   # emits an `add` call; only `noop` is allowed, so every fix whiffs
    loop = AgentLoop({"frozen": True}, state, [_Stuck()], {"noop": lambda **kw: {"ok": True}},
                     connector=conn, max_steps=100)
    result = loop.run()
    assert result.ok is False
    # Parks once the same error-list has recurred _STUCK_REPEATS times — well before max_steps (100).
    from maestro.agent_loop import _STUCK_REPEATS
    assert result.steps < _STUCK_REPEATS + 10
    assert result.failures and result.failures[0].code == "unfixable"


def test_flaky_but_progressing_slot_not_parked(tmp_path):
    # A slot that whiffs a few times between successes must not be parked: each success changes the
    # error SET (one fewer slot), so the same snapshot never recurs enough to look stuck.
    state = RunState(tmp_path)

    class _FlakyAdd:
        def __init__(self):
            self.i = 0

        def generate_with_tools(self, messages, schemas, **kw):
            self.i += 1
            return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
                "name": "add", "arguments": f'{{"id": "item_{self.i}"}}'}}]}}]}

    fails = {"n": 0}

    def add(id, **kw):
        fails["n"] += 1
        if fails["n"] % 4 != 0:   # 3 whiffs, then a success, repeatedly (< the stuck threshold)
            return {"ok": False, "error": "rejected"}
        items = state.read_component("items") or {"ids": []}
        items["ids"].append(id)
        state.write_component("items", items)
        return {"ok": True}

    loop = AgentLoop({"frozen": True}, state, [_Counter3()], {"add": add},
                     connector=_FlakyAdd(), max_steps=60)
    result = loop.run()
    assert result.ok is True   # never parked: the snapshot changes each time a slot lands


def test_flat_error_count_with_changing_set_not_parked(tmp_path):
    # The regression the snapshot detector fixes: authoring that SPAWNS downstream demand keeps the
    # error COUNT flat while the SET changes every step (a beat add removes a min_beats slot but
    # adds a scene slot). The old len-based park killed builds like this after 6 flat steps.
    state = RunState(tmp_path)

    def _widgets(chk, m, ctx):
        n = len((ctx.artifact.get("widgets") or {}).get("ids", []))
        return checks.slot_errors(max(0, 8 - n), type=chk.tier, code=chk.code,
                                  component="widgets", noun="widget")

    def _gadgets(chk, m, ctx):   # one gadget owed per widget already authored (demand grows)
        w = len((ctx.artifact.get("widgets") or {}).get("ids", []))
        g = len((ctx.artifact.get("gadgets") or {}).get("ids", []))
        return checks.slot_errors(max(0, w - g), type=chk.tier, code=chk.code,
                                  component="gadgets", noun="gadget")

    class _Producer(Module):
        id = "_producer"; component = "widgets"; priority = 10
        mode_prompt = "nodes_write.txt"; mode_tools = frozenset({"add_widget"})
        checks = [Check("widgets", _widgets, guard={"count_tool": "add_widget", "id_key": "id",
                        "id_list_key": "ids", "noun": "widget"})]
        def view(self, art): return {"ids": (art.get("widgets") or {}).get("ids", []), "open_slots": None}
        def render_context(self, ctx): return "add"

    class _Consumer(Module):
        id = "_consumer"; component = "gadgets"; priority = 20
        mode_prompt = "nodes_write.txt"; mode_tools = frozenset({"add_gadget"})
        checks = [Check("gadgets", _gadgets, guard={"count_tool": "add_gadget", "id_key": "id",
                        "id_list_key": "ids", "noun": "gadget"})]
        def view(self, art): return {"ids": (art.get("gadgets") or {}).get("ids", []), "open_slots": None}
        def render_context(self, ctx): return "add"

    class _Conn:
        def __init__(self, st): self.st = st; self.i = 0
        def generate_with_tools(self, messages, schemas, **kw):
            self.i += 1
            w = len((self.st.read_component("widgets") or {}).get("ids", []))
            name = "add_widget" if w < 8 else "add_gadget"   # widgets first (lower priority)
            return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
                "name": name, "arguments": f'{{"id": "{name}_{self.i}"}}'}}]}}]}

    def _adder(comp):
        def add(id, **kw):
            c = state.read_component(comp) or {"ids": []}
            c["ids"].append(id); state.write_component(comp, c)
            return {"ok": True, "id": id}
        return add

    loop = AgentLoop({"frozen": True}, state, [_Producer(), _Consumer()],
                     {"add_widget": _adder("widgets"), "add_gadget": _adder("gadgets")},
                     connector=_Conn(state), max_steps=60)
    result = loop.run()
    assert result.ok is True                                   # not parked despite 8 flat-count steps
    assert len(state.read_component("widgets")["ids"]) == 8
    assert len(state.read_component("gadgets")["ids"]) == 8


# ── parallel create prompts differentiate by slot ordinal ────────────────────
def test_guarded_create_prompt_names_its_slot(tmp_path):
    state = RunState(tmp_path)
    mod = _Counter3()
    ctx = build_context({"params": {}}, state)
    err = checks.slot_errors(3, type=ErrorType.BUILD, code="need3", component="items",
                             noun="item")[1]
    p0 = mod.get_correction_prompt(ctx, err, slot=0)
    p1 = mod.get_correction_prompt(ctx, err, slot=1)
    assert "PARALLEL AUTHORING" not in p0.user
    assert "PARALLEL AUTHORING" in p1.user and "#2" in p1.user


# ── empty response (reasoning overran max_tokens) retries once with reasoning off ──
class _EmptyThenToolConn:
    def __init__(self):
        self.calls = []

    def generate_with_tools(self, messages, schemas, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            return {"choices": [{"message": {"content": ""}}]}   # truncated: all reasoning
        return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "add", "arguments": '{"id": "item_1"}'}}]}}]}


def test_empty_response_retries_with_reasoning_off(tmp_path):
    from maestro.services import Services
    from maestro.modules.module import CorrectionPrompt
    state = RunState(tmp_path)
    added = []
    conn = _EmptyThenToolConn()
    services = Services(conn, {"add": lambda id, **kw: added.append(id) or {"ok": True}},
                        {"frozen": True}, state, budget=5)
    services.run(CorrectionPrompt("s", "u", ("add",)))
    assert added == ["item_1"]
    assert len(conn.calls) == 2 and conn.calls[1].get("reasoning") == "none"


# ── salvage: model emits args as content text, not a tool call ────────────────
from maestro.services import salvage_tool_call, parse_action

_WRITE_NODE_SCHEMA = {"type": "function", "function": {
    "name": "write_node",
    "parameters": {"type": "object",
                   "properties": {"node_id": {}, "content": {}, "story_state_delta": {}},
                   "required": ["node_id", "content"]}}}
_EDIT_NODE_SCHEMA = {"type": "function", "function": {
    "name": "edit_node",
    "parameters": {"type": "object",
                   "properties": {"node_id": {}, "line_index": {}, "text": {}, "speaker": {}},
                   "required": ["node_id"]}}}


def test_salvage_unique_match():
    content = '{"node_id": "beat_04", "content": {"lines": []}, "story_state_delta": {}}'
    tc = salvage_tool_call(content, [_WRITE_NODE_SCHEMA, _EDIT_NODE_SCHEMA])
    assert tc is not None
    assert tc["function"]["name"] == "write_node"
    assert json.loads(tc["function"]["arguments"])["node_id"] == "beat_04"


def test_salvage_strips_code_fence():
    content = '```json\n{"node_id": "b", "content": {}}\n```'
    tc = salvage_tool_call(content, [_WRITE_NODE_SCHEMA])
    assert tc is not None and tc["function"]["name"] == "write_node"


def test_salvage_bails_on_foreign_keys():
    # extra key not in any tool's properties -> not a clean fit -> no salvage
    content = '{"node_id": "b", "content": {}, "bogus": 1}'
    assert salvage_tool_call(content, [_WRITE_NODE_SCHEMA]) is None


def test_salvage_bails_when_required_missing():
    # only edit_node's required (node_id) is satisfiable, but content key is foreign to edit_node
    content = '{"line_index": 0, "text": "hi"}'
    assert salvage_tool_call(content, [_WRITE_NODE_SCHEMA, _EDIT_NODE_SCHEMA]) is None


def test_salvage_bails_on_prose():
    assert salvage_tool_call("I will now write the node.", [_WRITE_NODE_SCHEMA]) is None


def test_parse_action_salvages_text_response():
    resp = {"choices": [{"message": {
        "content": '{"node_id": "beat_04", "content": {"lines": []}}'}}]}
    action = parse_action(resp, [_WRITE_NODE_SCHEMA])
    assert action == {"tool": "write_node", "args": {"node_id": "beat_04", "content": {"lines": []}}}


def test_parse_action_no_schemas_no_salvage():
    resp = {"choices": [{"message": {"content": '{"node_id": "x", "content": {}}'}}]}
    assert parse_action(resp) == {}


def test_crossref_character_error_gets_kind_specific_prompt():
    """A hallucinated speaker (kind=character) gets its OWN fix prompt + the cast roster — not the
    generic menu (which spun 100+ steps in prod). Shared across scenes/world: a `component=places`
    (world-owned) node-line ref still routes to the node tools + the character prompt."""
    from maestro import context_render as cr
    from maestro.modules import scenes, world
    art = {"characters": {"characters": [{"id": "aris_thorne", "name": "Aris"}]},
           "nodes": {"node_ids": ["end1"], "nodes": {
               "end1": {"lines": [{"speaker": "mara_lin", "text": "hi"}], "end": {"type": "end"}}}}}
    ctx = _ctx({"params": {}, "modules": ["cast", "world", "scenes"]}, art)
    # component=places mimics the live world build (world owns this game's crossref).
    err = Error(type=ErrorType.FIX, code="crossref", component="places",
                message="nodes[end1].lines[0].speaker: 'mara_lin' is not a declared character",
                path="nodes[end1].lines[0].speaker", ref="mara_lin", kind="character")
    cp = cr.crossref_correction(world.MODULE, ctx, err)
    assert "COMMON FAILURES" not in cp.system         # not the generic menu
    assert "never an object" in cp.system.lower()      # the kind-specific speaker rule
    assert "aris_thorne" in cp.user                    # the roster to pick from
    assert "mara_lin" in cp.user                       # the target names the bad ref
    assert "edit_node" in cp.allowed_tools             # tools from the ref's slice (nodes), not places
