import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy._script import _stage_positions, _postprocess_script


def test_stage_positions_spread():
    assert _stage_positions({"a": None}) == {"a": "center"}
    assert _stage_positions({"a": None, "b": None}) == {"a": "left", "b": "right"}
    assert _stage_positions({"a": None, "b": None, "c": None}) == {
        "a": "left", "b": "right", "c": "center"}


def test_stage_positions_honors_explicit():
    # b is explicitly right → a fills the next free slot (left), not center.
    assert _stage_positions({"a": None, "b": "right"}) == {"a": "left", "b": "right"}


def test_postprocess_places_bare_shows():
    script = "\n".join([
        "label s1:",
        "    scene bg_room",
        "    show alex",
        "    show mara",
        '    alex "Where were you?"',
    ])
    out = _postprocess_script(script, {"alex", "mara"})

    # Bare shows get a slot (no more silent center pile-up).
    assert "show alex at left" in out
    assert "show mara at right" in out
    # The speaker line re-stages both with speaking/not_speaking alpha.
    assert "show alex at left, speaking" in out
    assert "show mara at right, not_speaking" in out


def test_postprocess_single_character_centered():
    script = "label s1:\n    show alex\n    alex \"Alone.\""
    out = _postprocess_script(script, {"alex"})
    assert "show alex at center" in out
