import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.spec import Spec


def test_component_roundtrip_and_artifact(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?"})
    state.write_component("node_scripts", {"node_ids": ["n1"]})

    assert state.read_component("premise") == {"central_question": "Q?"}
    artifact = state.load_artifact()
    assert set(artifact) == {"premise", "node_scripts"}
    assert artifact["premise"]["central_question"] == "Q?"


def test_load_artifact_excludes_reserved_files(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"x": 1})
    state.write_spec({"frozen": False})
    state.write_story_state({"established_facts": []})

    artifact = state.load_artifact()
    assert set(artifact) == {"premise"}
    assert state.component_ids() == ["premise"]


def test_reserved_name_rejected_as_component(tmp_path):
    state = RunState(tmp_path)
    with pytest.raises(ValueError):
        state.write_component("spec", {"oops": True})


def test_spec_roundtrip(tmp_path):
    state = RunState(tmp_path)
    state.write_spec({"title": "T", "frozen": True, "components": []})
    spec = Spec(state.read_spec())
    assert spec.title == "T"
    assert spec.frozen is True


def test_run_dir_under_working_directory(tmp_path, monkeypatch):
    import tools.execution_context as ec
    monkeypatch.setattr(ec, "resolve_base_path", lambda p=None: tmp_path)
    state = RunState.for_run("abc123")
    assert state.run_dir == tmp_path / "runs" / "abc123"
    assert state.run_dir.is_dir()


def test_state_wiring_fix_offers_all_host_tools(tmp_path):
    # A flag consumed in a place is naturally SET by a node choice — so the wiring fix must offer
    # node tools even for a places-attributed error, or the model can't produce it (live-build thrash).
    from maestro.modules.state import MODULE as STATE
    from maestro.modules.module import Error, ErrorType
    from maestro.modules.context import build_context
    state = RunState(tmp_path)
    state.write_component("places", {"place_ids": ["p1"],
                                     "places": {"p1": {"kind": "room", "interactables": []}}})
    ctx = build_context(Spec({"title": "T", "frozen": True,
                              "modules": ["world", "scenes", "state"], "params": {}}).data, state)
    err = Error(type=ErrorType.FIX, code="state_wiring", component="places",
                message="flag 'x' read but never produced", ref="x")
    cp = STATE.get_correction_prompt(ctx, err)
    assert "read_node" in cp.allowed_tools and "read_place" in cp.allowed_tools
    assert "add_effect" in cp.allowed_tools and "add_gate" in cp.allowed_tools


def test_state_wiring_fix_shows_full_wiring_report(tmp_path):
    # The fixer must see EVERY still-unwired value, or it re-gates another value's wiring to fix
    # this one and the hole just moves (live-build oscillation).
    from maestro.modules.state import MODULE as STATE
    from maestro.modules.module import Error, ErrorType
    from maestro.modules.context import build_context
    state = RunState(tmp_path)
    state.write_component("items", {"items": [{"id": "item_a", "name": "A"},
                                              {"id": "item_b", "name": "B"}]})
    ctx = build_context(Spec({"title": "T", "frozen": True,
                              "modules": ["world", "inventory", "state"], "params": {}}).data, state)
    err = Error(type=ErrorType.FIX, code="state_wiring", component="items",
                message="item 'item_a' ...", ref="item_a")
    cp = STATE.get_correction_prompt(ctx, err)
    assert "WIRING REPORT" in cp.user
    assert "item_a" in cp.user and "item_b" in cp.user   # the sibling hole is visible too
    # the failing host is excluded from upstream, so the fix must carry the catalog itself
    assert "CURRENT CATALOG" in cp.user and "never an empty list" in cp.user
