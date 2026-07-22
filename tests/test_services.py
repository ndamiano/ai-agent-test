"""Services — the LIMITS half of the build-loop contract.

The loop hands a module's Fix a fresh `Services` and lets the module drive its shape; Services owns
the caps that keep a fix from running away: the per-fix step budget (an uncatchable BudgetExhausted),
the per-step tool-scope gate, and the slot guard that keeps count-driven writes additive + in order.
These are the promises a fix relies on but never sees the internals of, so they're asserted here on
observable behaviour (return values + which tool actually ran), never on private call order.
"""

import pytest

from maestro.modules.module import CorrectionPrompt
from maestro.services import (
    BudgetExhausted,
    Services,
    _create_guard,
    parse_action,
    salvage_tool_call,
)

# ── boundary fakes: no live LLM, ever ─────────────────────────────────────────

class FakeConn:
    """A connector stand-in: returns a queued response per generate_with_tools call and records the
    kwargs (so a test can see reasoning/max_tokens without asserting call order)."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_with_tools(self, messages, schemas, **kw):
        self.calls.append(kw)
        return self._responses.pop(0) if self._responses else {"choices": [{"message": {}}]}


def _tool_call_response(name, args_json):
    return {"choices": [{"message": {"tool_calls": [
        {"id": "1", "type": "function", "function": {"name": name, "arguments": args_json}}]}}]}


_ADD_SCHEMA = {"type": "function", "function": {
    "name": "add_item",
    "parameters": {"type": "object",
                   "properties": {"item_id": {}, "name": {}},
                   "required": ["item_id"]}}}
_OTHER_SCHEMA = {"type": "function", "function": {
    "name": "write_node",
    "parameters": {"type": "object",
                   "properties": {"node_id": {}, "content": {}},
                   "required": ["node_id", "content"]}}}


# ── BudgetExhausted: uncatchable + fires at the cap ───────────────────────────

def test_budget_exhausted_is_baseexception_not_exception():
    # WHY: the whole point of the type is that a naive `while True: ... except Exception` fix CANNOT
    # swallow it — if it were an Exception, a runaway fix would loop forever past its cap.
    assert issubclass(BudgetExhausted, BaseException)
    assert not issubclass(BudgetExhausted, Exception)


def test_infer_raises_budget_exhausted_at_cap():
    # WHY: the per-fix step budget is the hard stop on LLM churn. With budget=1, the 1st infer is
    # allowed; the 2nd must raise BudgetExhausted so control unwinds to the loop instead of spending
    # more calls.
    conn = FakeConn([_tool_call_response("add_item", '{"item_id": "i1"}'),
                     _tool_call_response("add_item", '{"item_id": "i2"}')])
    svc = Services(conn, {}, {"frozen": True}, None, budget=1)
    svc.infer(["m"], [])                       # 1st call: within budget
    with pytest.raises(BudgetExhausted):
        svc.infer(["m"], [])                   # 2nd call: budget spent -> unwind
    assert len(conn.calls) == 1                # the over-budget call never reaches the connector


def test_infer_uncatchable_by_bare_except_exception():
    # WHY: prove the guarantee end-to-end — a fix body that wraps its loop in `except Exception`
    # still unwinds. This is the actual failure mode the BaseException choice defends against.
    conn = FakeConn([])
    svc = Services(conn, {}, {"frozen": True}, None, budget=0)  # budget=0 -> first infer raises
    def greedy_fix():
        while True:
            try:
                svc.infer(["m"], [])
            except Exception:
                continue   # a real fix's naive retry; must NOT trap BudgetExhausted
    with pytest.raises(BudgetExhausted):
        greedy_fix()


# ── dispatch: per-step tool-scope enforcement ─────────────────────────────────

def test_dispatch_refuses_off_scope_tool():
    # WHY: a small model reads other tool names from the prompt prose and calls an off-phase tool
    # (e.g. write_node while the step authors an item). dispatch must refuse it with an error dict —
    # not run it — so the model can't thrash across phases. The tool fn must never be invoked.
    ran = []
    tools = {"add_item": lambda **kw: ran.append(kw) or {"ok": True},
             "write_node": lambda **kw: ran.append(kw) or {"ok": True}}
    svc = Services(FakeConn([]), tools, {"frozen": True}, None, budget=5)
    svc.allowed = frozenset({"add_item"})
    res = svc.dispatch("write_node", {"node_id": "n1"})
    assert res.get("ok") is False and "not available for this step" in res["error"]
    assert ran == []                            # refused before dispatch — no side effect


def test_dispatch_allows_in_scope_tool():
    # WHY: the flip side — a tool that IS scoped for the step dispatches and returns its result.
    ran = []
    tools = {"add_item": lambda **kw: ran.append(kw) or {"ok": True, "wrote": kw["item_id"]}}
    svc = Services(FakeConn([]), tools, {"frozen": True}, None, budget=5)
    svc.allowed = frozenset({"add_item"})
    res = svc.dispatch("add_item", {"item_id": "sword"})
    assert res == {"ok": True, "wrote": "sword"}
    assert ran == [{"item_id": "sword"}]


def test_dispatch_no_scope_allows_everything():
    # WHY: allowed=None means "no step scope set" (the default) — dispatch must not gate, or a fix
    # that never set a scope would be unable to call any tool.
    tools = {"anything": lambda **kw: {"ok": True}}
    svc = Services(FakeConn([]), tools, {"frozen": True}, None, budget=5)
    assert svc.allowed is None
    assert svc.dispatch("anything", {}) == {"ok": True}


def test_dispatch_unknown_tool_reports_error():
    # WHY: an in-scope name with no registered fn is a wiring bug, surfaced as an error dict rather
    # than an exception that would crash the step.
    svc = Services(FakeConn([]), {}, {"frozen": True}, None, budget=5)
    res = svc.dispatch("ghost", {})
    assert "unknown tool" in res["error"]


# ── salvage_tool_call: recover a tool call from loose content ─────────────────

def test_salvage_unique_match_rebuilds_call():
    # WHY: local models sometimes emit a tool's args as raw JSON in message content instead of a
    # function call. When the keys uniquely fit ONE schema, rebuild the call so the work isn't
    # thrown away on a wasted nudge round-trip.
    tc = salvage_tool_call('{"item_id": "i1", "name": "Key"}', [_ADD_SCHEMA, _OTHER_SCHEMA])
    assert tc is not None
    assert tc["function"]["name"] == "add_item"


@pytest.mark.parametrize("content", ["not json at all", "", "{}", '{"nope": 1}'])
def test_salvage_junk_returns_none(content):
    # WHY: when content isn't a confident single-tool match (junk, empty, no required keys), salvage
    # must bail (None) and let the nudge path handle it — never guess a tool.
    assert salvage_tool_call(content, [_ADD_SCHEMA, _OTHER_SCHEMA]) is None


def test_salvage_ambiguous_match_returns_none():
    # WHY: if the same keys satisfy TWO schemas, salvaging would pick arbitrarily — bail instead.
    dup = {"type": "function", "function": {
        "name": "add_item_2",
        "parameters": {"type": "object", "properties": {"item_id": {}, "name": {}},
                       "required": ["item_id"]}}}
    assert salvage_tool_call('{"item_id": "i1"}', [_ADD_SCHEMA, dup]) is None


# ── parse_action: normalize a connector response into {tool, args} ────────────

def test_parse_action_from_tool_call():
    # WHY: the normal path — a real tool_call becomes {tool, args} with args parsed to a dict.
    resp = _tool_call_response("add_item", '{"item_id": "sword"}')
    assert parse_action(resp, [_ADD_SCHEMA]) == {"tool": "add_item", "args": {"item_id": "sword"}}


def test_parse_action_salvages_from_content():
    # WHY: no tool_calls but content carries a uniquely-matching arg blob -> parse_action must fall
    # through to salvage rather than reporting "no action".
    resp = {"choices": [{"message": {"content": '{"item_id": "i1", "name": "Key"}'}}]}
    action = parse_action(resp, [_ADD_SCHEMA, _OTHER_SCHEMA])
    assert action == {"tool": "add_item", "args": {"item_id": "i1", "name": "Key"}}


@pytest.mark.parametrize("resp", [
    {"error": "boom"},                                          # connector error
    {"choices": [{"message": {"content": "just chatting"}}]},   # plain prose, unsalvageable
    {"choices": [{"message": {}}]},                             # empty message
])
def test_parse_action_no_tool_returns_empty(resp):
    # WHY: an error, plain prose, or an empty message all mean "no tool call" — parse_action returns
    # {} so the caller (Services.run) can retry/report rather than dispatch garbage.
    assert parse_action(resp, [_ADD_SCHEMA]) == {}


# ── _create_guard: additive, in-order slot writes ────────────────────────────

def _guarded(existing, assigned=None):
    """Build a guard over a fake dispatch + view. Returns (guarded_fn, calls_list)."""
    calls = []
    dispatch = lambda name, args: calls.append((name, args)) or {"ok": True}
    view_fn = lambda: {"ids": list(existing)}
    g = _create_guard(dispatch, view_fn, tool="add_item", id_key="item_id",
                      id_list_key="ids", noun="item", assigned=assigned)
    return g, calls


def test_guard_refuses_overwrite_of_existing_id():
    # WHY: the guard makes a count-driven target grow by DESIGN — rewriting an existing id would
    # never raise the count and could stall progress. It must refuse and NOT dispatch the write.
    g, calls = _guarded({"sword"})
    res = g("add_item", {"item_id": "sword"})
    assert res["ok"] is False and "already exists" in res["error"]
    assert calls == []                          # never reached the real write


def test_guard_forces_the_assigned_slot():
    # WHY: prompt and guard share one pre-batch snapshot; a write to any id but the assigned one is
    # rejected so parallel siblings can't collide on slots. The wrong-slot write must not dispatch.
    g, calls = _guarded({"sword"}, assigned={"id": "shield"})
    res = g("add_item", {"item_id": "helmet"})
    assert res["ok"] is False and "assigned slot" in res["error"]
    assert calls == []


def test_guard_writes_the_assigned_new_id():
    # WHY: the happy path — a NEW id equal to the assigned slot passes straight through to the real
    # dispatch, growing the graph by one.
    g, calls = _guarded({"sword"}, assigned={"id": "shield"})
    res = g("add_item", {"item_id": "shield"})
    assert res == {"ok": True}
    assert calls == [("add_item", {"item_id": "shield"})]


def test_guard_passes_through_non_target_tools():
    # WHY: the guard wraps ONE create tool; every other tool the fix might call (reads, validate)
    # must pass through untouched, or a guarded step could only ever call its write tool.
    g, calls = _guarded({"sword"}, assigned={"id": "shield"})
    res = g("read_component", {"component_id": "x"})
    assert res == {"ok": True}
    assert calls == [("read_component", {"component_id": "x"})]


def test_guard_allows_entry_when_no_existing():
    # WHY: entry is exempt from the assigned-slot check (`existing` empty) so the guard can't
    # deadlock the very first write when nothing exists yet.
    g, calls = _guarded(set(), assigned={"id": "shield"})
    res = g("add_item", {"item_id": "first_thing"})
    assert res == {"ok": True}
    assert calls == [("add_item", {"item_id": "first_thing"})]


# ── Services.run: end-to-end retry-on-empty (guards the whole contract wiring) ─

def test_run_retries_with_reasoning_off_on_empty_then_dispatches():
    # WHY: an empty response usually means reasoning overran max_tokens and truncated the call. run
    # must retry once with reasoning="none" and then dispatch the recovered tool call — turning a
    # wasted step into the intended write.
    added = []
    conn = FakeConn([
        {"choices": [{"message": {"content": ""}}]},              # truncated: all reasoning
        _tool_call_response("add_item", '{"item_id": "i1"}'),     # retry lands the call
    ])
    svc = Services(conn, {"add_item": lambda **kw: added.append(kw["item_id"]) or {"ok": True}},
                   {"frozen": True}, None, budget=5)
    svc.run(CorrectionPrompt("sys", "user", ("add_item",)))
    assert added == ["i1"]
    assert conn.calls[1].get("reasoning") == "none"
