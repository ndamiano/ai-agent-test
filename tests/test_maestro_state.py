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
    state.write_scratchpad(current_goal="do thing")
    state.write_story_state({"established_facts": []})

    artifact = state.load_artifact()
    assert set(artifact) == {"premise"}
    assert state.component_ids() == ["premise"]


def test_reserved_name_rejected_as_component(tmp_path):
    state = RunState(tmp_path)
    with pytest.raises(ValueError):
        state.write_component("spec", {"oops": True})


def test_scratchpad_replaces_and_has_forced_fields(tmp_path):
    state = RunState(tmp_path)
    state.write_scratchpad(current_goal="first", recent_decisions=["a"])
    state.write_scratchpad(current_goal="second")  # replace, not append

    pad = state.read_scratchpad()
    assert pad["current_goal"] == "second"
    assert pad["recent_decisions"] == []          # replaced
    assert set(pad) == {"current_goal", "recent_decisions", "open_questions"}


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
