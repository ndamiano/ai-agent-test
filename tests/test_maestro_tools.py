import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools, SpecNotFrozen
from maestro.executor import Executor


def _spec(frozen=True):
    return Spec({"title": "T", "frozen": frozen, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "exists", "path": "premise.central_question"},
            {"type": "count", "path": "premise.characters", "min": 2},
        ]},
    ]})


def test_write_component_refuses_when_unfrozen(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(frozen=False), state)
    with pytest.raises(SpecNotFrozen):
        tools["write_component"]("premise", {"x": 1})


def test_write_and_read_component(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    assert tools["write_component"]("premise", {"central_question": "Q?"})["ok"] is True
    got = tools["read_component"]("premise")
    assert got["ok"] is True and got["content"]["central_question"] == "Q?"
    assert tools["read_component"]("missing")["ok"] is False


def test_edit_node_surgical_replace(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    state.write_component("node_scripts", {
        "node_ids": ["s1"],
        "scripts": {"s1": 'label s1:\n    a "he said hi"\n    jump s2'},
    })

    res = tools["edit_node"]("s1", "he said hi", "she said hi")
    assert res["ok"] is True
    s1 = state.read_component("node_scripts")["scripts"]["s1"]
    assert "she said hi" in s1
    assert "jump s2" in s1          # the rest of the node (incl. its jump) is untouched


def test_write_node_rejects_undefined_speaker(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    state.write_component("premise", {"central_question": "Q?",
                                      "characters": [{"id": "elias_voss"}, {"id": "yuki_tanaka"}]})

    # 'elias_vanaka' is a typo for the real 'elias_voss' — must be caught at write time, not
    # left to surface as a compile NameError dozens of steps later.
    res = tools["write_node"]("scene_01", 'label scene_01:\n    elias_vanaka "Shoot it."')
    assert res["ok"] is False
    assert "elias_vanaka" in res["error"] and "elias_voss" in res["error"]
    assert state.read_component("node_scripts") is None    # nothing persisted


def test_write_node_accepts_defined_speakers_narration_and_keywords(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    state.write_component("premise", {"central_question": "Q?",
                                      "characters": [{"id": "elias_voss"}, {"id": "yuki_tanaka"}]})

    res = tools["write_node"]("scene_01",
        'label scene_01:\n'
        '    scene bg_jungle\n'
        '    show elias_voss\n'
        '    "The forest went quiet."\n'           # narration: no speaker
        '    elias_voss "Positions!"\n'
        '    yuki_tanaka "Wait."\n'
        '    jump scene_02')
    assert res["ok"] is True


def test_edit_node_rejects_patch_that_introduces_bad_speaker(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    state.write_component("premise", {"central_question": "Q?",
                                      "characters": [{"id": "elias_voss"}]})
    state.write_component("node_scripts", {
        "node_ids": ["s1"], "scripts": {"s1": 'label s1:\n    elias_voss "hi"'}})

    res = tools["edit_node"]("s1", "elias_voss", "elias_vanaka")
    assert res["ok"] is False and "elias_vanaka" in res["error"]
    assert 'elias_voss "hi"' in state.read_component("node_scripts")["scripts"]["s1"]  # unchanged


def test_read_node(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    state.write_component("node_scripts", {"node_ids": ["s1"], "scripts": {"s1": 'label s1:\n    a "hi"'}})

    got = tools["read_node"]("s1")
    assert got["ok"] is True and 'a "hi"' in got["content"]
    assert tools["read_node"]("nope")["ok"] is False


def test_edit_node_errors(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    state.write_component("node_scripts", {"node_ids": ["s1"], "scripts": {"s1": 'label s1:\n    a "x"'}})

    assert tools["edit_node"]("missing", "a", "b")["ok"] is False        # no such node
    res = tools["edit_node"]("s1", "not-present", "b")                    # find not in node
    assert res["ok"] is False
    # The error echoes the node verbatim so the next attempt copies an exact snippet.
    assert 'a "x"' in res["error"]


def test_validate_tool_reports_failures(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["validate"]()
    assert res["ok"] is False
    assert {f["component_id"] for f in res["failures"]} == {"premise"}

    tools["write_component"]("premise", {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]})
    assert tools["validate"]()["ok"] is True


def test_compile_renpy_tool_delegates(tmp_path, monkeypatch):
    import renpy.compiler as compiler
    monkeypatch.setattr(compiler, "compile_renpy", lambda wd, **kw: {"ok": True, "reason": None})
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["compile_renpy"]()["ok"] is True


def test_generate_asset_wraps_image_gen(tmp_path, monkeypatch):
    import renpy.fns as fns
    monkeypatch.setattr(fns, "generate_images", lambda inputs, wd: {"status": "ok", "generated": ["bg_x.png"]})
    tools = build_tools(_spec(), RunState(tmp_path))
    res = tools["generate_asset"]()
    assert res["ok"] is True and res["generated"] == ["bg_x.png"]


def test_generate_asset_refuses_when_unfrozen(tmp_path):
    tools = build_tools(_spec(frozen=False), RunState(tmp_path))
    with pytest.raises(SpecNotFrozen):
        tools["generate_asset"]()


def test_update_scratchpad(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["update_scratchpad"](current_goal="write premise", open_questions=["which tone?"])
    pad = state.read_scratchpad()
    assert pad["current_goal"] == "write premise"
    assert pad["open_questions"] == ["which tone?"]


def test_request_review_returns_pending(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    res = tools["request_review"]("Romance or tragedy?", ["romance", "tragedy"])
    assert res["status"] == "review_requested"
    assert res["options"] == ["romance", "tragedy"]


def test_executor_drives_real_tools_to_completion(tmp_path):
    """Phase 3 + 4 together: executor + real artifact tools, agent authors content."""
    state = RunState(tmp_path)
    spec = _spec()
    tools = build_tools(spec, state)

    def decide(ctx):
        if any(f["component_id"] == "premise" for f in ctx["todo"]):
            return {"tool": "write_component", "args": {
                "component_id": "premise",
                "content": {"central_question": "Will it hold?", "characters": [{"id": "a"}, {"id": "b"}]},
            }}
        return {}

    result = Executor(spec, state, tools, decide, max_steps=5).run()
    assert result.ok is True
    assert result.failures == []


def test_write_node_accepts_flat_delta_kwargs(tmp_path):
    # The model passes delta fields flat (entity_updates=...) instead of nested; this
    # used to crash with TypeError. Now folded into the delta.
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["write_node"]("scene_01", "label scene_01:\n    return",
                              new_facts=["a clue surfaced"],
                              entity_updates={"mara": {"trust": "wary"}})
    assert res["ok"] is True
    ss = state.read_story_state()
    assert "a clue surfaced" in ss["established_facts"]
    assert ss["entity_states"]["mara"]["trust"] == "wary"


def test_write_node_tolerates_malformed_delta(tmp_path):
    # The model sometimes passes story_state_delta as a list/string, not a dict.
    # Must not crash (this hit a ValueError live).
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    assert tools["write_node"]("scene_01", "label scene_01:\n    return",
                               story_state_delta=["new_facts"])["ok"] is True
    assert tools["write_node"]("scene_02", "label scene_02:\n    return",
                               story_state_delta="oops")["ok"] is True


def test_write_node_normalizes_over_escaped_script(tmp_path):
    # Model over-escaped: literal \n and \" instead of real newline/quote. Normalize it.
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", 'label scene_01:\\n    evelyn \\"Hi.\\"\\n    return')
    script = state.read_component("node_scripts")["scripts"]["scene_01"]
    assert "\\n" not in script and '\\"' not in script
    assert script == 'label scene_01:\n    evelyn "Hi."\n    return'


def test_write_component_normalizes_node_scripts(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_component"]("node_scripts", {
        "node_ids": ["s1"], "scripts": {"s1": 'label s1:\\n    a \\"hi\\"\\n    return'}})
    s = state.read_component("node_scripts")["scripts"]["s1"]
    assert s == 'label s1:\n    a "hi"\n    return'


def _spec_with_dep():
    # premise is depended upon by node_scripts → premise is lockable; node_scripts is a leaf.
    return Spec({"title": "T", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "count", "path": "premise.characters", "min": 2},
        ]},
        {"id": "node_scripts", "deps": ["premise"], "done_conditions": [
            {"type": "count", "path": "node_scripts.node_ids", "min": 1},
            {"type": "compiles"},  # the compiles gate marks the terminal/leaf component
        ]},
    ]})


def test_passing_component_locks_against_rewrite(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec_with_dep(), state)
    # First write makes premise pass → it locks.
    assert tools["write_component"]("premise", {"characters": [{"id": "a"}, {"id": "b"}]})["ok"]
    # A second rewrite of the now-passing, depended-upon component is refused.
    res = tools["write_component"]("premise", {"characters": [{"id": "a"}]})
    assert res["ok"] is False and "locked" in res["error"]
    # The original content survives — the bad rewrite was not persisted.
    assert len(state.read_component("premise")["characters"]) == 2


def test_failing_depended_component_not_locked(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec_with_dep(), state)
    # premise written but still failing (only 1 char) → not locked, rewrite allowed.
    tools["write_component"]("premise", {"characters": [{"id": "a"}]})
    assert tools["write_component"]("premise", {"characters": [{"id": "a"}, {"id": "b"}]})["ok"]


def test_leaf_component_never_locks(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec_with_dep(), state)
    # node_scripts carries a `compiles` check → terminal/leaf → never locks, stays
    # rewritable (and the lock test short-circuits before running a real compile).
    assert tools["write_component"]("node_scripts", {"node_ids": ["s1"], "scripts": {"s1": "x"}})["ok"]
    assert tools["write_component"]("node_scripts", {"node_ids": ["s1", "s2"], "scripts": {"s1": "x", "s2": "y"}})["ok"]
