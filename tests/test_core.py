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
        def read_scratchpad(self): return {}
        def read_waivers(self): return []
        def read_human_todos(self): return []
    return Context(spec=spec, state=S(), artifact=art)


def test_cast_errors_param_driven():
    cast = MODULE_REGISTRY["cast"]
    art = {"characters": {"characters": [{"id": "a", "name": "A"}]}}
    # floor 1 -> satisfied; floor 2 -> a BUILD min_characters error
    assert not any(e.code == "min_characters" for e in cast.get_errors(_ctx({"params": {}}, art)))
    errs = cast.get_errors(_ctx({"params": {"min_characters": 2}}, art))
    assert any(e.code == "min_characters" and e.type is ErrorType.BUILD for e in errs)


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
    story = {"story": {"central_question": "Q", "beats": [{"id": "b1"}],
                       "endings": [{"id": "e1"}]}}
    params = {"params": {"each_node_min_lines": 1, "min_branches": 1}}

    light = {e.code for e in scenes.get_errors(_ctx(params, dict(nodes)))}
    rich = {e.code for e in scenes.get_errors(_ctx(params, {**nodes, **story}))}

    narrative = {"beats_realized", "endings_are_nodes", "min_branches", "all_characters_speak"}
    assert not (narrative & light)        # no story -> no narrative floor
    assert narrative & rich               # story present -> narrative floor fires


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

from maestro.modules.scenes import _stamp_beat, pick_slot
from maestro.services import _create_guard


def test_pick_slot_indexed_in_dramatic_order():
    view = {"open_slots": [{"id": "z_late", "beat": "beat_02"}, {"id": "a_early", "beat": "beat_01"}],
            "beats": [{"id": "beat_01"}, {"id": "beat_02"}]}
    assert pick_slot(view, 0)["id"] == "a_early"
    assert pick_slot(view, 1)["id"] == "z_late"
    assert pick_slot(view, 2) is None


def test_create_guard_enforces_the_assigned_slot():
    calls = []
    ok = lambda name, args: (calls.append((name, args)), {"ok": True})[1]
    view = {"node_ids": ["n1"], "open_slots": [{"id": "s1"}, {"id": "s2"}], "beats": []}
    g = _create_guard(ok, lambda: view, "write_node", "node_id", "node_ids", "node",
                      assigned={"id": "s2", "beat": "beat_02"}, prepare=_stamp_beat)
    refused = g("write_node", {"node_id": "s1"})
    assert refused["ok"] is False and "'s2'" in refused["error"]
    g("write_node", {"node_id": "s2"})
    assert calls and calls[0][1]["node_id"] == "s2"
    assert calls[0][1]["beat"] == "beat_02"   # system stamps the assigned slot's beat


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
    "story": {"central_question": "Can the farm be saved?",
              "endings": [{"id": "ending_saved", "description": "she keeps it"}],
              "beats": [{"id": "b1", "summary": "the notice arrives"}]},
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
    assert "central question: Can the farm be saved?" in p.user
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
    assert result.steps < 15                      # parked long before max_steps
    assert result.failures and result.failures[0].code == "unfixable"


def test_progress_unparks_attempt_counters(tmp_path):
    # Slot identities are positional (#001 persists while siblings land), so counters reset on
    # any forward progress — a retryable authoring slot must not get parked mid-build.
    state = RunState(tmp_path)

    class _FlakyAdd:
        """Every write attempt fails 5x then succeeds — under the cap only because progress
        (an eventual success) clears the counters."""
        def __init__(self):
            self.i = 0

        def generate_with_tools(self, messages, schemas, **kw):
            self.i += 1
            return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
                "name": "add", "arguments": f'{{"id": "item_{self.i}"}}'}}]}}]}

    fails = {"n": 0}

    def add(id, **kw):
        fails["n"] += 1
        if fails["n"] % 6 != 0:   # 5 failures, then one success, repeatedly
            return {"ok": False, "error": "rejected"}
        items = state.read_component("items") or {"ids": []}
        items["ids"].append(id)
        state.write_component("items", items)
        return {"ok": True}

    loop = AgentLoop({"frozen": True}, state, [_Counter3()], {"add": add},
                     connector=_FlakyAdd(), max_steps=60)
    result = loop.run()
    assert result.ok is True   # never parked: each success resets the counters


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
