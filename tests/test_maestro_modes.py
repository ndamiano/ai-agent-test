import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.executor import Executor
from maestro.agent import (_filter_schemas, _schemas_for_target, _prompt_for_target,
                           _load_prompt, make_subloop, _create_guard)
from maestro.context_render import pick_slot as _pick_slot
from maestro.discrete.dialogue import (_render_context, _render_slot_focus)
from maestro.discrete.cast import _render_context as _render_premise_context
from maestro.discrete.dialogue import SPINE as DIALOGUE
from maestro.discrete.navigation import MODULE as NAVIGATION
from maestro.modules import compose, PRESETS
from maestro.tools import TOOL_SCHEMAS

# The per-mode/per-target gating moved from agent.py globals onto the owning Module; these aliases
# read it from the composed bundle so the tests exercise the same behavior through the new seam.
_MODE_TOOLS = compose(PRESETS["vn"].modules).mode_tools          # mode -> allowed tools
_TARGET_TOOLS = DIALOGUE.target_tools                            # node per-target gating
_TARGET_PROMPT = DIALOGUE.target_jobs                            # node check-type -> job
_NODE_PROMPTS = {job: _load_prompt(fn) for job, fn in DIALOGUE.prompts.items()}


def _schemas_for_mode(mode, schemas):
    return _filter_schemas(_MODE_TOOLS.get(mode), schemas)


def make_node_subloop(connector=None, component_guide="", cap=20):
    return make_subloop(DIALOGUE, connector=connector, component_guide=component_guide, cap=cap)


def make_place_subloop(connector=None, component_guide="", cap=20):
    return make_subloop(NAVIGATION, connector=connector, component_guide=component_guide, cap=cap)


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


def test_render_context_premise_is_title_request_and_own_todo():
    # Premise mode gets the title + request + ONLY premise's failing checks — no spec dump, no
    # other components' to-do, no skeleton noise.
    ctx = {"mode": "premise",
           "spec": {"title": "Attic", "request": "two sisters clear their dead mother's house"},
           "todo": [{"component_id": "premise", "check": {"type": "count"}, "detail": "need 3 chars"},
                    {"component_id": "nodes", "check": {"type": "count"}, "detail": "need 5 nodes"}],
           "upstream": {"asset_manifest": {"backgrounds": ["bg"]}}}
    rendered = _render_premise_context(ctx)
    assert rendered.startswith("TITLE: Attic\n\nREQUEST: two sisters clear")
    assert "count: need 3 chars" in rendered          # premise's own check
    assert "need 5 nodes" not in rendered              # other component's check withheld
    assert "asset_manifest" not in rendered and "SPEC" not in rendered


def test_outline_context_view_drops_beats_keeps_anchors():
    # The full beat list must NOT reach the node author as locked upstream — it gets the beat
    # window from slot focus instead. The outline's context_view (the upstream trimmer) drops
    # beats; logline + ending_paths stay.
    from maestro.discrete.outline import _outline_view
    v = _outline_view({
        "logline": "two sisters, one house",
        "beats": [{"id": "beat_01", "summary": "secret summary"}],
        "ending_paths": [{"ending": "sell", "earned_by": "x"}]})
    assert v == {"logline": "two sisters, one house",
                 "ending_paths": [{"ending": "sell", "earned_by": "x"}]}
    assert "beats" not in v


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


def test_create_target_exposes_only_write_node():
    names = {s["function"]["name"]
             for s in _schemas_for_target({"check": {"type": "beats_realized"}},
                                          _node_schemas(), _TARGET_TOOLS)}
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
    assert p("beats_realized") is _NODE_PROMPTS["author"]
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
        target={"component_id": "nodes", "check": {"type": "beats_realized"}, "detail": "x"},
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
    # The subloop handed the connector tools gated to the target (beats_realized → write_node only).
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


def test_node_subloop_create_rejects_overwrite():
    # While creating scenes (beats_realized) the model keeps rewriting scene_01 — an overwrite
    # fills no new slot, so it's rejected and steered to a new id; only new ids reach dispatch.
    tc = {"id": "tc", "function": {
        "name": "write_node", "arguments": '{"node_id": "scene_01", "content": "label scene_01:"}'}}
    conn = _FakeConn([{"choices": [{"message": {"content": None, "tool_calls": [tc]}}]}])
    runner = make_node_subloop(connector=conn, cap=3)
    dispatched, reports = [], []
    runner(
        target={"component_id": "nodes", "check": {"type": "beats_realized"}, "detail": "x"},
        context={"todo": []},
        dispatch=lambda action: (dispatched.append(action) or {"ok": True}),
        target_met=lambda: False,
        report=lambda s: reports.append(s),
        budget=3,
        view_fn=lambda: {"node_ids": ["scene_01"]},   # scene_01 already exists
    )
    assert dispatched == []                             # overwrite never reached the real tool
    assert any("already exists" in r for r in reports)


def test_node_subloop_create_allows_new_id():
    tc = {"id": "tc", "function": {
        "name": "write_node", "arguments": '{"node_id": "scene_02", "content": "label scene_02:"}'}}
    conn = _FakeConn([{"choices": [{"message": {"content": None, "tool_calls": [tc]}}]}])
    runner = make_node_subloop(connector=conn, cap=1)
    dispatched = []
    runner(
        target={"component_id": "nodes", "check": {"type": "beats_realized"}, "detail": "x"},
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


# ── slot-driven authoring: the count guard makes the node graph grow by design ───────────────

def _guard(view):
    """Wrap a recording dispatch with the count guard over a fixed view."""
    seen = []
    def dispatch(action):
        seen.append(action)
        return {"ok": True, "node_id": action["args"].get("node_id")}
    guarded = _create_guard(dispatch, lambda: view, "write_node", "node_id", "node_ids", "node")
    return guarded, seen


def test_guard_entry_node_allowed_when_nothing_written():
    guarded, seen = _guard({"node_ids": [], "open_slots": []})
    assert guarded({"tool": "write_node", "args": {"node_id": "scene_01"}})["ok"] is True
    assert seen  # dispatched


def test_guard_rejects_node_that_is_not_the_assigned_slot():
    view = {"node_ids": ["scene_01"], "open_slots": [{"id": "scene_02", "from": []}]}
    guarded, seen = _guard(view)
    res = guarded({"tool": "write_node", "args": {"node_id": "scene_99"}})
    assert res["ok"] is False and "assigned slot" in res["error"] and "scene_02" in res["error"]
    assert not seen  # never dispatched


def test_guard_allows_filling_the_assigned_slot():
    view = {"node_ids": ["scene_01"], "open_slots": [{"id": "scene_02", "from": []}]}
    guarded, seen = _guard(view)
    assert guarded({"tool": "write_node", "args": {"node_id": "scene_02"}})["ok"] is True
    assert seen[0]["args"]["node_id"] == "scene_02"


def test_guard_enforces_the_one_system_picked_slot():
    # Two open slots; the system picks the one whose beat comes first. The other is rejected
    # even though it is a valid open slot — the author writes the spine in order.
    view = {"node_ids": ["scene_01"],
            "beats": [{"id": "beat_01"}, {"id": "beat_02"}],
            "open_slots": [{"id": "scene_03", "from": [], "beat": "beat_02"},
                           {"id": "scene_02", "from": [], "beat": "beat_01"}]}
    guarded, seen = _guard(view)
    assert guarded({"tool": "write_node", "args": {"node_id": "scene_03"}})["ok"] is False
    assert not seen
    assert guarded({"tool": "write_node", "args": {"node_id": "scene_02"}})["ok"] is True


def test_guard_stamps_the_assigned_slots_beat():
    # The system owns the beat: writing the picked slot, the guard injects its beat into args so
    # the author never sets it.
    view = {"node_ids": ["scene_01"],
            "beats": [{"id": "beat_01"}, {"id": "beat_02"}],
            "open_slots": [{"id": "scene_02", "from": [], "beat": "beat_02"}]}
    guarded, seen = _guard(view)
    assert guarded({"tool": "write_node", "args": {"node_id": "scene_02"}})["ok"] is True
    assert seen[0]["args"]["beat"] == "beat_02"


def test_guard_stamps_first_beat_on_opening_node():
    view = {"node_ids": [], "open_slots": [], "beats": [{"id": "beat_01"}, {"id": "beat_02"}]}
    guarded, seen = _guard(view)
    guarded({"tool": "write_node", "args": {"node_id": "scene_01"}})
    assert seen[0]["args"]["beat"] == "beat_01"


def test_guard_rejects_overwriting_existing_node():
    view = {"node_ids": ["scene_01"], "open_slots": [{"id": "scene_02", "from": []}]}
    guarded, _ = _guard(view)
    res = guarded({"tool": "write_node", "args": {"node_id": "scene_01"}})
    assert res["ok"] is False and "already exists" in res["error"]


def test_guard_no_deadlock_when_no_open_slots():
    # Nodes exist but every target is written → a fresh branch root is allowed (escape hatch).
    view = {"node_ids": ["scene_01", "scene_02"], "open_slots": []}
    guarded, seen = _guard(view)
    assert guarded({"tool": "write_node", "args": {"node_id": "scene_03"}})["ok"] is True
    assert seen


def test_guard_ignores_slot_rule_for_views_without_open_slots():
    # A places-style view (no open_slots key) keeps only the no-overwrite rule.
    view = {"node_ids": ["r1"]}
    guarded, seen = _guard(view)
    assert guarded({"tool": "write_node", "args": {"node_id": "r2"}})["ok"] is True


def test_render_slot_focus_shows_one_slot_and_beat_window():
    view = {
        "node_ids": ["scene_01", "scene_02"],
        "beats": [{"id": "beat_01", "summary": "they arrive", "tension": "why now?"},
                  {"id": "beat_02", "summary": "they argue", "tension": "who decides?"},
                  {"id": "beat_03", "summary": "the break", "tension": "can it heal?"}],
        "open_slots": [{
            "id": "scene_03",
            "beat": "beat_02",
            "from": [{"node": "scene_02", "label": "go to the attic"}],
            "path": [{"id": "scene_01", "synopsis": "they arrive"},
                     {"id": "scene_02", "synopsis": "they pack"}],
        }],
    }
    text = "\n".join(_render_slot_focus(view))
    assert "node_id = scene_03" in text and "go to the attic" in text
    assert "they arrive" in text and "they pack" in text          # path breadcrumb
    assert "beat_01" in text and "beat_02" in text and "beat_03" in text  # prev/this/next
    assert "THIS NODE'S BEAT" in text and "they argue" in text  # beat_02's summary


def test_render_slot_focus_opening_node_when_nothing_written():
    view = {"node_ids": [], "open_slots": [],
            "beats": [{"id": "beat_01", "summary": "the arrival"}]}
    text = "\n".join(_render_slot_focus(view))
    assert "OPENING NODE" in text and "FIRST BEAT" in text and "the arrival" in text


def test_pick_slot_orders_by_beat():
    view = {"beats": [{"id": "beat_01"}, {"id": "beat_02"}],
            "open_slots": [{"id": "s_b", "beat": "beat_02"}, {"id": "s_a", "beat": "beat_01"}]}
    assert _pick_slot(view)["id"] == "s_a"
    assert _pick_slot({"open_slots": []}) is None
