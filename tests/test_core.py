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
    art = {"nodes": {"node_ids": ["a", "b"], "nodes": {
        "a": {"end": {"type": "jump", "target": "b"}}, "b": {"end": {"type": "end"}}}}}
    assert checks.reachable_from_start(art)[0] is True
    art["nodes"]["nodes"]["a"]["end"] = {"type": "end"}  # b now orphaned
    assert checks.reachable_from_start(art)[0] is False


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
    refs = {r["ref"] for r in checks.state_wiring(art)}
    assert "orphan" in refs        # produced, never consumed -> use it or cut it
    assert "never_set" in refs     # consumed, never produced -> dangling

    # A flag both set and gated is fully wired -> no error.
    ok = {"nodes": {"node_ids": ["n1", "n2"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "x", "effects": [{"set_flag": "f"}]}],
               "end": {"type": "jump", "target": "n2"}},
        "n2": {"lines": [{"speaker": "a", "text": "y"}], "end": {"type": "menu", "choices": [
            {"text": "go", "target": "n1", "requires": {"flag": "f"}},
            {"text": "stay", "target": "n1"}]}}}}}
    assert checks.state_wiring(ok) == []


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

    def get_correction_prompt(self, ctx, error):
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
