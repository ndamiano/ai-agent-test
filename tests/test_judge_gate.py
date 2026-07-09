import sys
from pathlib import Path

# eval/ lives at the repo root, not under src/ (which conftest already adds).
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.judge import _apply_gates, _levels_to_scores, _overall  # noqa: E402


def _rubric(gate=None):
    coherence = {"name": "coherence", "weight": 2.5,
                 "levels": {str(i): str(i) for i in range(5)}}
    if gate is not None:
        coherence["gate"] = gate
    return {
        "scale": {"min": 0, "max": 4, "display_multiplier": 25},
        "criteria": [
            coherence,
            {"name": "craft", "weight": 1.0, "levels": {str(i): str(i) for i in range(5)}},
        ],
    }


def _score(rubric, coherence_level, craft_level):
    raw = {"coherence": {"level": coherence_level}, "craft": {"level": craft_level}}
    scores = _levels_to_scores(raw, rubric)
    return _apply_gates(_overall(scores, rubric), scores, rubric)


def test_gate_below_threshold_caps_overall_at_gate_score():
    # A gated criterion below its threshold caps the overall at its OWN score — so fluent
    # nonsense (coherence 0) cannot ride perfect craft to a decent number.
    r = _rubric(gate={"threshold": 2})
    assert _score(r, 0, 4) == 0.0
    assert _score(r, 1, 4) == 25.0


def test_gate_at_or_above_threshold_is_inert():
    # At/above threshold the normal weighted overall stands; the gate never inflates.
    r = _rubric(gate={"threshold": 2})
    ungated = _score(_rubric(gate=None), 2, 3)
    assert _score(r, 2, 3) == ungated  # gate is a cap, not an override


def test_no_gate_field_is_a_noop():
    # A rubric without a gate behaves exactly as before this feature existed.
    r = _rubric(gate=None)
    assert _score(r, 0, 4) == round((0 * 2.5 + 100 * 1.0) / 3.5, 2)
