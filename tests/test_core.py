"""The new module core: checks, per-module get_errors, params resolution, composition + human,
error prioritization, waivers, and the agent loop driving to completion."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.modules import checks, compose
from maestro.modules.context import Context, build_context
from maestro.modules.module import (CorrectionPrompt, Error, ErrorType, MODULE_REGISTRY,
                                     idkey, register_module, Module)
from maestro.agent_loop import AgentLoop, effective_pairs, prioritize
from maestro.modules import human
from tools.spec_tools import _resolve_params


# ── checks ───────────────────────────────────────────────────────────────────
def test_count_check_min():
    art = {"premise": {"characters": [{"id": "a"}]}}
    ok, _ = checks.count(art, "premise.characters", min=2)
    assert ok is False
    ok, _ = checks.count(art, "premise.characters", min=1)
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
    vn = _resolve_params(["cast", "dialogue"], {"each_node_min_lines": 9})
    assert vn["min_characters"] == 2          # dialogue raises cast's floor of 1
    assert vn["each_node_min_lines"] == 9     # proposer raise above floor 6
    assert "voice" in vn["premise_fields"]    # list floors UNION
    # proposer may not lower below floor
    assert _resolve_params(["cast", "dialogue"], {"each_node_min_lines": 2})["each_node_min_lines"] == 6


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
    art = {"premise": {"central_question": "Q", "characters": [{"id": "a", "name": "A"}]}}
    # floor 1 -> satisfied; floor 2 -> a BUILD min_characters error
    assert not any(e.code == "min_characters" for e in cast.get_errors(_ctx({"params": {}}, art)))
    errs = cast.get_errors(_ctx({"params": {"min_characters": 2}}, art))
    assert any(e.code == "min_characters" and e.type is ErrorType.BUILD for e in errs)


# ── compose always includes human; prioritization ───────────────────────────
def test_compose_includes_human():
    ids = [m.id for m in compose(("cast", "dialogue"))]
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


# ── the author_loop Fix: a create-target driven one item per call ────────────
from functools import partial
from maestro.services import author_loop


class _Counter(Module):
    """Needs 2 items; its create-target fix is the author_loop adding one per call."""
    id = "_counter_test"
    component = "items"
    mode_prompt = "write_node.txt"      # any existing prompt; content irrelevant to the test
    mode_tools = frozenset({"add"})

    def get_errors(self, ctx):
        n = len((ctx.artifact.get("items") or {}).get("ids", []))
        return [] if n >= 2 else [Error(ErrorType.BUILD, "need_items", "items", "need 2 items")]

    def view(self, artifact):
        return {"ids": (artifact.get("items") or {}).get("ids", []), "open_slots": None}

    def render_context(self, ctx):
        return "add an item"

    def get_fix(self, context, error):
        return partial(author_loop, context, error, module=self,
                       guard={"count_tool": "add", "id_key": "id", "id_list_key": "ids",
                              "noun": "item"})


class _AddConn:
    """Returns an `add` tool call with a fresh id each call."""
    def __init__(self):
        self.i = 0

    def generate_with_tools(self, messages, schemas, **kw):
        self.i += 1
        return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "add", "arguments": f'{{"id": "item_{self.i}"}}'}}]}}]}


def test_author_loop_drives_create_target_to_green(tmp_path):
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
