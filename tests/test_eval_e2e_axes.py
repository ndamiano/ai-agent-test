import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval import judge, report, failures
from eval.climb import _score_run
from eval.runner import _build_gate
from pipelines.renpy.e2e_view import e2e_artifact

# axis A holds 3 criteria, axis B holds 1 — unequal on purpose so that
# mean-of-axes and flat-mean-of-criteria give different numbers.
_RUBRIC = {
    "axes": ["a", "b"],
    "criteria": [
        {"name": "a1", "axis": "a", "description": "x"},
        {"name": "a2", "axis": "a", "description": "x"},
        {"name": "a3", "axis": "a", "description": "x"},
        {"name": "b1", "axis": "b", "description": "x"},
    ],
}

_FLAT_RUBRIC = {
    "criteria": [
        {"name": "c1", "description": "x", "weight": 1.0},
        {"name": "c2", "description": "x", "weight": 3.0},
    ],
}


def _scores(**vals):
    return {name: {"score": v, "reasoning": "r"} for name, v in vals.items()}


def test_axis_overall_is_mean_of_axes_not_flat_mean():
    scores = _scores(a1=60, a2=60, a3=60, b1=100)
    # flat mean = 70; mean of axis means = mean(60, 100) = 80
    assert judge._axis_overall(scores, _RUBRIC) == 80.0


def test_axis_means_reports_each_axis():
    means = judge.axis_means(_scores(a1=40, a2=80, a3=60, b1=90), _RUBRIC)
    assert means == {"a": 60.0, "b": 90.0}


def test_has_axes_distinguishes_rubric_shapes():
    assert judge._has_axes(_RUBRIC)
    assert not judge._has_axes(_FLAT_RUBRIC)


def test_score_prompt_groups_by_axis():
    prompt = judge._score_prompt("e2e", {"foo": "bar"}, _RUBRIC)
    assert "A axis:" in prompt and "B axis:" in prompt
    # axis rubrics compute overall as mean of axis means, not a weighted flat average
    assert "mean of the axis means" in prompt


def test_flat_rubric_keeps_weighted_average_prompt():
    prompt = judge._score_prompt("premise", {"foo": "bar"}, _FLAT_RUBRIC)
    assert "weighted average" in prompt
    assert "axis" not in prompt.lower()


def test_score_prompt_embeds_raw_artifact_string():
    script = 'label start:\n    eve "Hi there"\n    menu:\n        "Go": jump a\n'
    prompt = judge._score_prompt("e2e", script, _RUBRIC)
    # the script is embedded verbatim, not JSON-encoded (no escaped newlines/quotes)
    assert script in prompt
    assert '\\n' not in prompt.split("Criteria:")[0]
    assert "built game script" in prompt


def test_e2e_artifact_reads_built_script(tmp_path):
    script_path = tmp_path / "game_output" / "game" / "script.rpy"
    script_path.parent.mkdir(parents=True)
    script_path.write_text('label start:\n    eve "Hello"\n', encoding="utf-8")
    assert e2e_artifact(tmp_path) == 'label start:\n    eve "Hello"\n'


def test_e2e_artifact_reads_saved_project_dir(tmp_path):
    # eval/games/<name> holds the project root directly: game/script.rpy (no game_output/)
    script_path = tmp_path / "game" / "script.rpy"
    script_path.parent.mkdir(parents=True)
    script_path.write_text('label start:\n    eve "Replay me"\n', encoding="utf-8")
    assert e2e_artifact(tmp_path) == 'label start:\n    eve "Replay me"\n'


def test_e2e_artifact_missing_returns_none(tmp_path):
    assert e2e_artifact(tmp_path) is None


def test_score_run_grades_artifact_for_e2e():
    captured = {}

    class FakeJudge:
        def score(self, stage_id, payload, rubric):
            captured["payload"] = payload
            return {"overall": 50.0, "scores": {}}

    results = [{"ok": True, "artifact": "label start:", "outputs": {"premise": {"x": 1}}}]
    _score_run(results, "e2e", _RUBRIC, FakeJudge(), e2e=True)
    # the judge sees the built script, never the production JSON
    assert captured["payload"] == "label start:"


def test_score_run_falls_back_to_outputs_without_artifact():
    captured = {}

    class FakeJudge:
        def score(self, stage_id, payload, rubric):
            captured["payload"] = payload
            return {"overall": 50.0, "scores": {}}

    results = [{"ok": True, "artifact": None, "outputs": {"premise": {"x": 1}}}]
    _score_run(results, "e2e", _RUBRIC, FakeJudge(), e2e=True)
    assert captured["payload"] == {"premise": {"x": 1}}


def test_summarize_emits_by_axis_distribution():
    scored = [
        {"overall": 80.0, "scores": _scores(a1=60, a2=60, a3=60, b1=100)},
        {"overall": 70.0, "scores": _scores(a1=40, a2=40, a3=40, b1=100)},
    ]
    run_results = [{"ok": True, "elapsed": 1.0}, {"ok": True, "elapsed": 1.0}]
    summary = report.summarize(run_results, scored, _RUBRIC)
    assert set(summary["by_axis"]) == {"a", "b"}
    assert summary["by_axis"]["a"]["mean"] == 50.0  # mean of 60 and 40
    assert summary["by_axis"]["b"]["mean"] == 100.0


def test_summarize_omits_by_axis_for_flat_rubric():
    scored = [{"overall": 70.0, "scores": _scores(c1=70, c2=70)}]
    summary = report.summarize([{"ok": True, "elapsed": 1.0}], scored, _FLAT_RUBRIC)
    assert "by_axis" not in summary


def test_build_gate_fails_on_lint_errors():
    outputs = {"build_result": {"lint": {"error_count": 3}}}
    assert _build_gate(outputs) == "build gate: 3 lint error(s)"


def test_build_gate_fails_on_dist_error_and_returncode():
    assert _build_gate({"build_result": {"lint": {"error_count": 0}, "dist_error": "boom"}}) == "build gate: boom"
    assert "returncode 1" in _build_gate({"build_result": {"lint": {"error_count": 0}, "dist_returncode": 1}})


def test_build_gate_passes_clean_build():
    assert _build_gate({"build_result": {"lint": {"error_count": 0}, "dist_returncode": 0}}) is None


def test_build_gate_passes_when_no_sdk():
    # error_count None means lint could not run (no SDK) — not a quality failure
    assert _build_gate({"build_result": {"lint": {"error_count": None}}}) is None


def test_classify_recognizes_build_gate():
    assert failures.classify({"ok": False, "error": "build gate: 2 lint error(s)"}) == "build_gate"


def test_classify_build_gate_surfaces_error_when_no_log():
    analysis = failures.analyze([{"ok": False, "error": "build gate: 2 lint error(s)"}])
    assert analysis["examples"]["build_gate"] == "build gate: 2 lint error(s)"


def test_real_e2e_rubric_is_well_formed():
    path = Path(__file__).parent.parent / "eval" / "rubrics" / "renpy_e2e.json"
    rubric = json.loads(path.read_text(encoding="utf-8"))
    axes = set(rubric["axes"])
    assert axes == {"narrative", "characters", "writing", "structure"}
    # the live e2e rubric is level-based: classification, not a calibrated float
    assert judge._is_levels(rubric)
    keys = [str(k) for k in range(rubric["scale"]["min"], rubric["scale"]["max"] + 1)]
    for c in rubric["criteria"]:
        assert c["name"].isidentifier(), c["name"]
        assert c["axis"] in axes, c["name"]
        assert len(c["description"]) > 20, c["name"]
        # every level 0..max is authored with a self-contained description
        assert set(c["levels"]) == set(keys), c["name"]
        assert all(c["levels"][k].strip() for k in keys), c["name"]
    # every declared axis is actually used
    used = {c["axis"] for c in rubric["criteria"]}
    assert used == axes
