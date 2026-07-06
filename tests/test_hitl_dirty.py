"""Epic A — the dirty core: sidecar store, dirty-emits-error, human-only thumb tools, and the
note→rewrite clear paths. Behaviour/contract level — no live services."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.tools import build_tools, TOOL_SCHEMAS
from maestro.services import Services
from maestro.modules import human as human_mod
from maestro.modules.human import MODULE as HUMAN
from maestro.modules.context import build_context


def _spec():
    return {"title": "T", "frozen": True, "modules": [], "params": {}}


def _seed_nodes(state):
    state.write_component("nodes", {"node_ids": ["n1"], "nodes": {"n1": {
        "lines": [{"speaker": "a", "text": "old one"}, {"speaker": "b", "text": "old two"}],
        "end": {"type": "end"}}}})


def _dirty_errors(state, spec=None):
    ctx = build_context(spec or _spec(), state)
    return [e for e in HUMAN.get_errors(ctx) if e.code == "dirty_asset"]


# ── A1: the store ───────────────────────────────────────────────────────────────
def test_set_dirty_adds_and_persists(tmp_path):
    state = RunState(tmp_path)
    human_mod.set_dirty(state, "nodes:n1", "make it tenser")

    # survives a fresh RunState pointed at the same dir (durable next to waivers)
    assert RunState(tmp_path).read_dirty() == [{"idkey": "nodes:n1", "note": "make it tenser"}]


def test_set_dirty_upserts_note(tmp_path):
    state = RunState(tmp_path)
    human_mod.set_dirty(state, "nodes:n1", "first")
    human_mod.set_dirty(state, "nodes:n1", "second")   # re-flag updates, never duplicates

    entries = human_mod.dirty_entries(state)
    assert entries == [{"idkey": "nodes:n1", "note": "second"}]


def test_clear_dirty_removes(tmp_path):
    state = RunState(tmp_path)
    human_mod.set_dirty(state, "nodes:n1", "x")
    human_mod.set_dirty(state, "characters:mara", "y")

    assert human_mod.clear_dirty(state, "nodes:n1") is True
    assert human_mod.clear_dirty(state, "nodes:n1") is False   # already gone
    assert human_mod.dirty_entries(state) == [{"idkey": "characters:mara", "note": "y"}]


def test_dirty_file_is_not_an_artifact_component(tmp_path):
    state = RunState(tmp_path)
    human_mod.set_dirty(state, "nodes:n1", "x")
    assert "dirty" not in state.load_artifact()
    assert "dirty" not in state.component_ids()


def test_asset_idkey_roundtrip():
    assert human_mod.asset_idkey("nodes", "scene_3") == "nodes:scene_3"
    assert human_mod.split_idkey("nodes:scene_3") == ("nodes", "scene_3")


# ── A2: dirty emits an error ─────────────────────────────────────────────────────
def test_dirty_emits_one_human_error_per_asset(tmp_path):
    from maestro.modules.module import ErrorType
    state = RunState(tmp_path)
    human_mod.set_dirty(state, "nodes:n1", "tighten the ending")
    human_mod.set_dirty(state, "characters:mara", "")

    errs = _dirty_errors(state)
    by_key = {(e.component, e.path): e for e in errs}
    assert set(by_key) == {("nodes", "n1"), ("characters", "mara")}
    assert all(e.type is ErrorType.HUMAN for e in errs)
    assert by_key[("nodes", "n1")].message == "tighten the ending"
    # an empty note still yields a usable rewrite instruction
    assert by_key[("characters", "mara")].message


def test_dirty_error_reaches_the_human_todo_list(tmp_path):
    # effective_failures is what Epic C's read endpoints surface — dirty must ride it.
    state = RunState(tmp_path)
    state.write_spec(_spec())
    human_mod.set_dirty(state, "nodes:n1", "note here")

    rows = human_mod.effective_failures(state.read_spec(), state)
    hit = [r for r in rows if r["code"] == "dirty_asset"]
    assert len(hit) == 1
    assert hit[0]["component"] == "nodes" and hit[0]["path"] == "n1"


# ── A3: human-only thumb tools ───────────────────────────────────────────────────
def test_thumb_tools_are_not_agent_facing():
    schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert {"set_dirty", "thumbs_up", "thumbs_down"} & schema_names == set()


def test_thumbs_down_sets_then_thumbs_up_clears(tmp_path):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    tools = build_tools(_spec(), state)

    tools["thumbs_down"]("nodes:n1", "rewrite the last beat")
    assert len(_dirty_errors(state)) == 1
    assert human_mod.dirty_entries(state)[0]["note"] == "rewrite the last beat"

    res = tools["thumbs_up"]("nodes:n1")
    assert res["cleared"] is True
    assert _dirty_errors(state) == []   # thumbs-up retires the error


def test_set_dirty_tool_and_thumbs_down_are_equivalent(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["set_dirty"]("places:zone_a", "n1")
    tools["thumbs_down"]("places:zone_a", "n2")   # same asset, updates note
    assert human_mod.dirty_entries(state) == [{"idkey": "places:zone_a", "note": "n2"}]


# ── A4: the note-driven rewrite clears the flag (node path via rewrite_node) ─────
class _FakeConn:
    def __init__(self, content):
        self.content = content

    def generate_with_tools(self, messages, schemas, **kw):
        assert any("make it tenser" in (m.get("content") or "") for m in messages)  # note flows in
        return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "write_node",
            "arguments": json.dumps({"node_id": "n1", "content": self.content})}}]}}]}


def test_node_dirty_cleared_by_rewrite(tmp_path):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    _seed_nodes(state)
    spec = _spec()
    tools = build_tools(spec, state)
    rewritten = {"lines": [{"speaker": "a", "text": "a far tenser beat"},
                           {"speaker": "b", "text": "the air goes cold"}], "end": {"type": "end"}}
    services = Services(_FakeConn(rewritten), tools, spec, state, budget=5)

    human_mod.set_dirty(state, "nodes:n1", "make it tenser")
    error = _dirty_errors(state, spec)[0]
    ctx = build_context(spec, state)
    HUMAN.get_fix(ctx, error)(services)

    assert state.read_component("nodes")["nodes"]["n1"] == rewritten   # rewritten from the note
    assert human_mod.dirty_entries(state) == []                        # and the flag is cleared


def test_node_dirty_not_cleared_when_rewrite_fails(tmp_path):
    # A rewrite that never lands a write must leave the flag set — the loop retries / human re-looks.
    class _NoCall:
        def generate_with_tools(self, messages, schemas, **kw):
            return {"choices": [{"message": {"content": "I refuse to call a tool."}}]}
    state = RunState(tmp_path)
    state.write_spec(_spec())
    _seed_nodes(state)
    spec = _spec()
    services = Services(_NoCall(), build_tools(spec, state), spec, state, budget=3)

    human_mod.set_dirty(state, "nodes:n1", "make it tenser")
    error = _dirty_errors(state, spec)[0]
    HUMAN.get_fix(build_context(spec, state), error)(services)

    assert human_mod.dirty_entries(state) == [{"idkey": "nodes:n1", "note": "make it tenser"}]


# ── A4: general (non-node) path clears on a successful mutating edit ─────────────
class _FakeServices:
    """A minimal stand-in for Services that fires one successful mutating tool call through the
    fix's dispatch, so we test the clear-on-successful-rewrite contract without an LLM."""
    def __init__(self, state, result):
        self.state = state
        self.spec = _spec()
        self.tools = {}
        self.conn = None
        self._result = result
        self.dispatched = []

    def _report(self, msg):
        pass

    def dispatch(self, name, args):
        self.dispatched.append(name)
        return self._result

    def run(self, prompt, *, dispatch=None):
        (dispatch or self.dispatch)("write_component", {"component_id": "characters"})


def test_non_node_dirty_cleared_on_successful_edit(tmp_path):
    state = RunState(tmp_path)
    services = _FakeServices(state, {"ok": True})
    human_mod.set_dirty(state, "characters:mara", "give her a scar")
    error = _dirty_errors(state)[0]

    HUMAN.get_fix(build_context(_spec(), state), error)(services)
    assert human_mod.dirty_entries(state) == []   # a successful mutating call clears the flag


def test_non_node_dirty_stays_when_edit_fails(tmp_path):
    # A rejected edit (dispatch returns a failure) must NOT clear the flag.
    state = RunState(tmp_path)
    services = _FakeServices(state, {"ok": False, "error": "nope"})
    services.dispatch = lambda name, args: {"ok": False, "error": "nope"}
    services.run = lambda prompt, dispatch=None: (dispatch or services.dispatch)("write_component", {})

    human_mod.set_dirty(state, "characters:mara", "give her a scar")
    error = _dirty_errors(state)[0]
    HUMAN.get_fix(build_context(_spec(), state), error)(services)
    assert human_mod.dirty_entries(state) == [{"idkey": "characters:mara", "note": "give her a scar"}]
