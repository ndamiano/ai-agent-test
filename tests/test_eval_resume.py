import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from eval import report


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    d = tmp_path / "results"
    monkeypatch.setattr(report, "RESULTS_DIR", d)
    return d


def _summary(p25):
    return {"n": 5, "overall": {"p25": p25, "mean": p25}, "by_criterion": {}}


def test_save_writes_scored_when_provided(results_dir):
    scored = [{"overall": 80, "scores": {}}, None]
    out = report.save("renpy", "node_scripts", "slice", _summary(80),
                      label="baseline", scored=scored)
    assert (out / "scored.json").exists()
    assert (out / "summary.json").exists()


def test_save_omits_scored_when_none(results_dir):
    out = report.save("renpy", "node_scripts", "slice", _summary(80), label="baseline")
    assert not (out / "scored.json").exists()


def test_load_latest_round_trips(results_dir):
    scored = [{"overall": 90, "scores": {"pacing": {"score": 90}}}]
    report.save("renpy", "node_scripts", "slice", _summary(90),
                label="baseline", scored=scored)
    loaded = report.load_latest("renpy", "node_scripts", "slice")
    assert loaded is not None
    _, summary, loaded_scored = loaded
    assert summary["overall"]["p25"] == 90
    assert loaded_scored == scored


def test_load_latest_picks_most_recent_by_mtime(results_dir):
    # Saved alphabetically out of order: "iter01" sorts after "baseline",
    # but the accepted iter is the newer run and must win on mtime.
    report.save("renpy", "node_scripts", "slice", _summary(60),
                label="baseline", scored=[{"overall": 60}])
    time.sleep(0.01)
    report.save("renpy", "node_scripts", "slice", _summary(75),
                label="iter01_accepted", scored=[{"overall": 75}])
    _, summary, _ = report.load_latest("renpy", "node_scripts", "slice")
    assert summary["overall"]["p25"] == 75


def test_load_latest_none_when_no_runs(results_dir):
    assert report.load_latest("renpy", "node_scripts", "slice") is None


def test_load_latest_skips_runs_without_scored(results_dir):
    # A run saved without scored.json is not resumable.
    report.save("renpy", "node_scripts", "slice", _summary(50), label="baseline")
    assert report.load_latest("renpy", "node_scripts", "slice") is None
