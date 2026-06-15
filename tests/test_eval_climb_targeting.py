import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval import judge, report

_RUBRIC = {
    "criteria": [
        {"name": "arc_movement", "axis": "narrative", "description": "protagonist changes"},
        {"name": "subtext", "axis": "characters", "description": "no announced feelings"},
        {"name": "distinctiveness", "axis": "characters", "description": "voices differ"},
    ],
}

_SUMMARY = {
    "overall": {"p25": 10.0, "mean": 20.0},
    "by_criterion": {
        "arc_movement": {"p25": 0.0},
        "subtext": {"p25": 25.0},
        "distinctiveness": {"p25": 40.0},
    },
}


def test_mutate_targets_global_weakest_without_filter():
    prompt = judge._mutate_prompt("PROMPT", _SUMMARY, _RUBRIC)
    # arc_movement has the lowest p25, so it is the unrestricted target
    assert "'arc_movement'" in prompt


def test_mutate_filter_restricts_target_to_allowed_criteria():
    # arc_movement is weaker but excluded; the weakest *allowed* is subtext
    prompt = judge._mutate_prompt("PROMPT", _SUMMARY, _RUBRIC, criteria=["subtext", "distinctiveness"])
    assert "'subtext'" in prompt
    assert "'arc_movement'" not in prompt


def test_mutate_filter_picks_weakest_within_allowed_set():
    prompt = judge._mutate_prompt("PROMPT", _SUMMARY, _RUBRIC, criteria=["distinctiveness"])
    assert "'distinctiveness'" in prompt


def test_flat_levels_overall_is_weighted_average_not_zero():
    # regression: a flat (no-axis) level rubric must aggregate via weighted average.
    # _axis_overall returns 0.0 with no axes, which silently zeroed every climb.
    rubric = {
        "scale": {"min": 0, "max": 4, "display_multiplier": 25},
        "criteria": [
            {"name": "subtext", "weight": 1.5, "levels": {str(k): "x" for k in range(5)}},
            {"name": "naturalism", "weight": 2.0, "levels": {str(k): "x" for k in range(5)}},
        ],
    }
    scores = {"subtext": {"score": 25.0}, "naturalism": {"score": 50.0}}
    overall = judge._overall(scores, rubric)
    assert overall > 0
    # weighted: (25*1.5 + 50*2.0) / 3.5 == 39.29
    assert overall == round((25 * 1.5 + 50 * 2.0) / 3.5, 2)


def test_save_mutation_persists_text_and_score(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "RESULTS_DIR", tmp_path)
    path = report.save_mutation("renpy", "node_scripts", "park", 1, 2,
                                "REWRITTEN PROMPT BODY", _SUMMARY, target="subtext")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "REWRITTEN PROMPT BODY" in text
    assert "p25=10.0" in text          # the score is recorded in the header
    assert "target=subtext" in text
    assert path.parent.name == "mutations"
