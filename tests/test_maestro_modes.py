import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.executor import Executor
from maestro.agent import _schemas_for_mode, _render_context, _MODE_TOOLS
from maestro.tools import TOOL_SCHEMAS


def _spec():
    return Spec({"title": "T", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "exists", "path": "premise.central_question"}]},
        {"id": "node_scripts", "deps": ["premise"], "done_conditions": [
            {"type": "count", "path": "node_scripts.node_ids", "min": 3}]},
    ]})


def _executor(state):
    return Executor(_spec(), state, tools={}, decide=lambda ctx: {}, max_steps=1)


def test_mode_is_earliest_failing_in_dep_order(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?"})  # passes
    ex = _executor(state)

    # premise passes, node_scripts fails → mode is node_scripts.
    todo = [{"component_id": "node_scripts", "check": {"type": "count"}, "detail": "too few"}]
    ctx = ex.build_context(todo=todo)
    assert ctx["mode"] == "node_scripts"

    # When premise also fails it comes first in dep order → it wins.
    todo2 = [
        {"component_id": "node_scripts", "check": {"type": "count"}, "detail": "x"},
        {"component_id": "premise", "check": {"type": "exists"}, "detail": "y"},
    ]
    assert ex.build_context(todo=todo2)["mode"] == "premise"


def test_upstream_holds_passing_components_only(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?", "characters": [{"id": "mara"}]})
    state.write_component("node_scripts", {"node_ids": [], "scripts": {}})
    ex = _executor(state)

    todo = [{"component_id": "node_scripts", "check": {"type": "count"}, "detail": "too few"}]
    ctx = ex.build_context(todo=todo)

    assert "premise" in ctx["upstream"]                     # passing → handed over
    assert ctx["upstream"]["premise"]["characters"][0]["id"] == "mara"
    assert "node_scripts" not in ctx["upstream"]            # still failing → withheld


def test_active_view_from_projector(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?"})  # passes → not the mode
    state.write_component("node_scripts", {"node_ids": ["s1"], "scripts": {"s1": "label s1:"}})

    proj = lambda artifact: {"node_ids": artifact.get("node_scripts", {}).get("node_ids", [])}
    ex = Executor(_spec(), state, tools={}, decide=lambda c: {}, max_steps=1,
                  projectors={"node_scripts": proj})

    todo = [{"component_id": "node_scripts", "check": {"type": "count"}, "detail": "x"}]
    ctx = ex.build_context(todo=todo)
    assert ctx["mode"] == "node_scripts"
    assert ctx["active_view"] == {"node_ids": ["s1"]}


def test_active_view_none_without_projector_for_mode(tmp_path):
    state = RunState(tmp_path)
    ex = _executor(state)  # no projectors
    todo = [{"component_id": "premise", "check": {"type": "exists"}, "detail": "y"}]
    assert ex.build_context(todo=todo)["active_view"] is None


def test_no_mode_when_nothing_failing(tmp_path):
    state = RunState(tmp_path)
    ex = _executor(state)
    ctx = ex.build_context(todo=[])
    assert ctx["mode"] is None


def test_schemas_filtered_for_node_mode():
    filtered = _schemas_for_mode("node_scripts", TOOL_SCHEMAS)
    names = {s["function"]["name"] for s in filtered}
    assert names == set(_MODE_TOOLS["node_scripts"])
    assert "write_component" not in names and "read_component" not in names


def test_schemas_unfiltered_for_unknown_mode():
    assert _schemas_for_mode(None, TOOL_SCHEMAS) is TOOL_SCHEMAS
    assert _schemas_for_mode("graph", TOOL_SCHEMAS) is TOOL_SCHEMAS  # no such mode


def test_premise_mode_allows_write_component():
    names = {s["function"]["name"] for s in _schemas_for_mode("premise", TOOL_SCHEMAS)}
    assert "write_component" in names and "write_node" not in names


def test_render_context_shows_locked_components():
    ctx = {"todo": [], "upstream": {"premise": {"characters": [{"id": "mara"}]}}}
    rendered = _render_context(ctx)
    assert "LOCKED COMPONENTS" in rendered and "mara" in rendered


def test_render_context_omits_locked_block_when_empty():
    assert "LOCKED COMPONENTS" not in _render_context({"todo": [], "upstream": {}})
