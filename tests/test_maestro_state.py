
import maestro.state
from maestro.state import RunState


def test_spec_roundtrip(tmp_path):
    state = RunState(tmp_path)
    state.write_spec({"title": "T", "mode": "2d", "frozen": True})
    spec = state.read_spec()
    assert spec["title"] == "T"
    assert spec["frozen"] is True


def test_run_dir_under_working_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda p=None: tmp_path)
    state = RunState("abc123")
    assert state.run_dir == tmp_path / "runs" / "abc123"
    assert state.run_dir.is_dir()
