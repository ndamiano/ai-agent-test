import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.run import create_run


def test_create_run_persists_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(tmp_path / rid)))
    run_id = create_run("u1")
    assert RunState(tmp_path / run_id).read_owner() == "u1"


def test_owner_survives_reload_and_is_not_an_artifact_component(tmp_path):
    state = RunState(tmp_path / "g")
    state.write_owner("u1")
    state.write_component("premise", {"q": 1})
    # owner.json is reserved — it must not leak into the assembled artifact / component list.
    assert state.component_ids() == ["premise"]
    assert "owner" not in state.load_artifact()
    assert RunState(tmp_path / "g").read_owner() == "u1"
