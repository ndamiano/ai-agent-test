import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.executor import Executor
from maestro.agent import (_schemas_for_mode, _schemas_for_target, _prompt_for_target,
                           _NODE_PROMPTS, _TARGET_PROMPT, _TARGET_TOOLS, _render_context,
                           _MODE_TOOLS, make_node_subloop)
from maestro.tools import TOOL_SCHEMAS


def _spec():
    return Spec({"title": "T", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "exists", "path": "premise.central_question"}]},
        {"id": "nodes", "deps": ["premise"], "done_conditions": [
            {"type": "count", "path": "nodes.node_ids", "min": 3}]},
    ]})


def _executor(state):
    return Executor(_spec(), state, tools={}, decide=lambda ctx: {}, max_steps=1)


def test_mode_is_earliest_failing_in_dep_order(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?"})  # passes
    ex = _executor(state)

    # premise passes, nodes fails → mode is nodes.
    todo = [{"component_id": "nodes", "check": {"type": "count"}, "detail": "too few"}]
    ctx = ex.build_context(todo=todo)
    assert ctx["mode"] == "nodes"

    # When premise also fails it comes first in dep order → it wins.
    todo2 = [
        {"component_id": "nodes", "check": {"type": "count"}, "detail": "x"},
        {"component_id": "premise", "check": {"type": "exists"}, "detail": "y"},
    ]
    assert ex.build_context(todo=todo2)["mode"] == "premise"


def test_upstream_holds_passing_components_only(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?", "characters": [{"id": "mara"}]})
    state.write_component("nodes", {"node_ids": [], "scripts": {}})
    ex = _executor(state)

    todo = [{"component_id": "nodes", "check": {"type": "count"}, "detail": "too few"}]
    ctx = ex.build_context(todo=todo)

    assert "premise" in ctx["upstream"]                     # passing → handed over
    assert ctx["upstream"]["premise"]["characters"][0]["id"] == "mara"
    assert "nodes" not in ctx["upstream"]            # still failing → withheld


def test_active_view_from_projector(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?"})  # passes → not the mode
    state.write_component("nodes", {"node_ids": ["s1"], "scripts": {"s1": "label s1:"}})

    proj = lambda artifact: {"node_ids": artifact.get("nodes", {}).get("node_ids", [])}
    ex = Executor(_spec(), state, tools={}, decide=lambda c: {}, max_steps=1,
                  projectors={"nodes": proj})

    todo = [{"component_id": "nodes", "check": {"type": "count"}, "detail": "x"}]
    ctx = ex.build_context(todo=todo)
    assert ctx["mode"] == "nodes"
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
    filtered = _schemas_for_mode("nodes", TOOL_SCHEMAS)
    names = {s["function"]["name"] for s in filtered}
    assert names == set(_MODE_TOOLS["nodes"])
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


def test_render_context_shows_last_read_and_stall_nudge():
    rendered = _render_context({"todo": [], "last_read": "read_node(s1)\nlabel s1:", "stalled": True})
    assert "LAST READ" in rendered and "label s1:" in rendered
    assert "STOP reading" in rendered


def test_render_context_no_stall_nudge_when_calm():
    assert "STOP reading" not in _render_context({"todo": [], "stalled": False})


def test_render_context_shows_single_target():
    rendered = _render_context({"todo": [], "target": {
        "component_id": "nodes", "check": {"type": "reachable_from_start"},
        "detail": "scene_03 unreachable"}})
    assert "YOUR TARGET" in rendered and "reachable_from_start" in rendered


def _node_schemas():
    return _schemas_for_mode("nodes", TOOL_SCHEMAS)


def test_count_target_exposes_only_write_node():
    names = {s["function"]["name"]
             for s in _schemas_for_target({"check": {"type": "count"}}, _node_schemas(), _TARGET_TOOLS)}
    assert names == {"write_node"}                       # can't waste a step reading/editing


def test_reachability_target_withholds_write_node():
    names = {s["function"]["name"]
             for s in _schemas_for_target({"check": {"type": "reachable_from_start"}},
                                          _node_schemas(), _TARGET_TOOLS)}
    assert names == {"read_node", "edit_node"}           # wire orphans, don't create more


def test_unknown_target_keeps_full_node_set():
    full = _node_schemas()
    assert _schemas_for_target({"check": {"type": "refs_resolve"}}, full, _TARGET_TOOLS) is full
    assert _schemas_for_target(None, full, _TARGET_TOOLS) is full


def test_prompt_routes_author_vs_fix():
    # Content/structure targets author fresh scenes; wiring/compile targets repair them.
    def p(t):
        return _prompt_for_target({"check": {"type": t}}, _NODE_PROMPTS, _TARGET_PROMPT)
    assert p("count") is _NODE_PROMPTS["author"]
    assert p("each_node_min_lines") is _NODE_PROMPTS["author"]
    assert p("reachable_from_start") is _NODE_PROMPTS["fix"]
    assert p("compiles") is _NODE_PROMPTS["fix"]
    assert _prompt_for_target(None, _NODE_PROMPTS, _TARGET_PROMPT) is _NODE_PROMPTS["author"]


class _FakeConn:
    def __init__(self, responses):
        self.responses, self.i, self.last_schemas = responses, 0, None
        self.reasoning = None
        self.reasoning_calls = []

    def generate_with_tools(self, messages, schemas, reasoning=None, max_tokens=None):
        self.last_schemas = schemas
        self.reasoning_calls.append(reasoning)
        r = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return r


def test_node_subloop_dispatches_and_stops_on_target():
    tool_call = {"id": "tc1", "function": {
        "name": "write_node", "arguments": '{"node_id": "s1", "content": "label s1:"}'}}
    conn = _FakeConn([{"choices": [{"message": {"content": None, "tool_calls": [tool_call]}}]}])
    runner = make_node_subloop(connector=conn, cap=5)

    dispatched, reports = [], []
    runner(
        target={"component_id": "nodes", "check": {"type": "count"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: (dispatched.append(action) or {"ok": True}),
        target_met=lambda: True,           # satisfied after the first write → loop stops
        report=lambda s: reports.append(s),
        budget=5,
        view_fn=lambda: None,
    )

    assert dispatched and dispatched[0]["tool"] == "write_node"
    assert reports and reports[0].startswith("write_node(s1)")
    assert conn.i == 1                      # stopped after target met, didn't keep looping
    # The subloop handed the connector tools gated to the target (count → write_node only).
    assert {s["function"]["name"] for s in conn.last_schemas} == {"write_node"}
    assert conn.reasoning_calls == [None]   # never escalated — it made progress immediately


def _writer_response():
    tc = {"id": "tc", "function": {
        "name": "write_node", "arguments": '{"node_id": "n", "content": "label n:"}'}}
    return {"choices": [{"message": {"content": None, "tool_calls": [tc]}}]}


def test_node_subloop_escalates_reasoning_after_stall():
    # Every dispatch errors → no progress. After 2 dead iterations reasoning turns on.
    conn = _FakeConn([_writer_response()])
    runner = make_node_subloop(connector=conn, cap=5)
    reports = []
    runner(
        target={"component_id": "nodes", "check": {"type": "count"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: {"error": "bad node id"},
        target_met=lambda: False,
        report=lambda s: reports.append(s),
        budget=5,
        view_fn=lambda: None,
    )
    assert conn.reasoning_calls[:2] == [None, None]   # cheap attempts first
    assert conn.reasoning_calls[2] == "high"          # escalated after STALL_LIMIT
    assert reports                                    # tool errors still reported per call


def test_fix_target_starts_escalated():
    # Repair targets (fix kind, e.g. compiles) need reasoning ON from the first call — without
    # it the model garbles labels instead of repointing jumps. No stall wait.
    tc = {"id": "tc", "function": {
        "name": "edit_node", "arguments": '{"node_id": "n", "content": "label n:"}'}}
    conn = _FakeConn([{"choices": [{"message": {"content": None, "tool_calls": [tc]}}]}])
    runner = make_node_subloop(connector=conn, cap=5)
    runner(
        target={"component_id": "nodes", "check": {"type": "compiles"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: {"ok": True},
        target_met=lambda: True,
        report=lambda s: None,
        budget=5,
        view_fn=lambda: None,
    )
    assert conn.reasoning_calls[0] == "high"   # escalated from the first iteration, no stall


def test_author_target_starts_unescalated():
    # Authoring fresh content keeps reasoning OFF until it stalls (the cheap-first floor).
    conn = _FakeConn([_writer_response()])
    runner = make_node_subloop(connector=conn, cap=5)
    runner(
        target={"component_id": "nodes", "check": {"type": "count"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: {"ok": True},
        target_met=lambda: True,
        report=lambda s: None,
        budget=5,
        view_fn=lambda: None,
    )
    assert conn.reasoning_calls[0] is None


def test_subloop_no_tool_call_is_visible_and_bails():
    # Model returns prose, no tool call (the rooms hang). Must report it, escalate, then give
    # up — never spin silently.
    from maestro.agent import make_place_subloop
    text_only = {"choices": [{"message": {"content": "{ a giant json blob }", "tool_calls": []}}]}
    conn = _FakeConn([text_only])
    runner = make_place_subloop(connector=conn, cap=10)
    reports = []
    runner(
        target={"component_id": "places", "check": {"type": "count"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: {"ok": True},
        target_met=lambda: False,
        report=lambda s: reports.append(s),
        budget=10,
        view_fn=lambda: None,
    )
    assert reports and all("no tool call" in r for r in reports)   # every dead turn is surfaced
    assert "high" in conn.reasoning_calls                          # escalated, didn't sit silent
    assert conn.i <= 5                                             # bailed, didn't burn the budget


def _reader_response():
    tc = {"id": "tc", "function": {"name": "read_node", "arguments": '{"node_id": "n"}'}}
    return {"choices": [{"message": {"content": None, "tool_calls": [tc]}}]}


def test_node_subloop_drops_reads_when_sightseeing():
    # read_node never changes the artifact, so a run of them is no progress even though each
    # call "succeeds". After STALL_LIMIT the brake trips: reasoning escalates AND read tools
    # are pulled so the model must act instead of touring the graph (the 13-read budget leak).
    conn = _FakeConn([_reader_response()])
    runner = make_node_subloop(connector=conn, cap=6)
    runner(
        target={"component_id": "nodes", "check": {"type": "each_node_min_lines"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: {"ok": True, "content": "label n:"},
        target_met=lambda: False,
        report=lambda s: None,
        budget=6,
        view_fn=lambda: None,
    )
    assert conn.reasoning_calls[:2] == [None, None]
    assert conn.reasoning_calls[2] == "high"          # reads don't reset stall → escalates
    read_names = {"read_node", "read_component", "read_story_state"}
    assert not (read_names & {s["function"]["name"] for s in conn.last_schemas})  # reads pulled


def test_node_subloop_count_rejects_overwrite():
    # During `count` the model keeps rewriting scene_01 — an overwrite never raises the count,
    # so it's rejected and steered to a new id; only genuinely new ids reach dispatch.
    tc = {"id": "tc", "function": {
        "name": "write_node", "arguments": '{"node_id": "scene_01", "content": "label scene_01:"}'}}
    conn = _FakeConn([{"choices": [{"message": {"content": None, "tool_calls": [tc]}}]}])
    runner = make_node_subloop(connector=conn, cap=3)
    dispatched, reports = [], []
    runner(
        target={"component_id": "nodes", "check": {"type": "count"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: (dispatched.append(action) or {"ok": True}),
        target_met=lambda: False,
        report=lambda s: reports.append(s),
        budget=3,
        view_fn=lambda: {"node_ids": ["scene_01"]},   # scene_01 already exists
    )
    assert dispatched == []                             # overwrite never reached the real tool
    assert any("already exists" in r for r in reports)


def test_node_subloop_count_allows_new_id():
    tc = {"id": "tc", "function": {
        "name": "write_node", "arguments": '{"node_id": "scene_02", "content": "label scene_02:"}'}}
    conn = _FakeConn([{"choices": [{"message": {"content": None, "tool_calls": [tc]}}]}])
    runner = make_node_subloop(connector=conn, cap=1)
    dispatched = []
    runner(
        target={"component_id": "nodes", "check": {"type": "count"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: (dispatched.append(action) or {"ok": True}),
        target_met=lambda: False,
        report=lambda s: None,
        budget=1,
        view_fn=lambda: {"node_ids": ["scene_01"]},
    )
    assert dispatched and dispatched[0]["args"]["node_id"] == "scene_02"


def test_node_subloop_no_escalation_while_progressing():
    # Each write lands cleanly → stall resets every iteration → reasoning stays off. (Target is
    # each_node_min_lines, where rewriting a node IS the job — count would reject the repeat.)
    conn = _FakeConn([_writer_response()])
    runner = make_node_subloop(connector=conn, cap=4)
    runner(
        target={"component_id": "nodes", "check": {"type": "each_node_min_lines"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: {"ok": True},
        target_met=lambda: False,
        report=lambda s: None,
        budget=4,
        view_fn=lambda: None,
    )
    assert all(r is None for r in conn.reasoning_calls)
