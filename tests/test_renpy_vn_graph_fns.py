"""Tests for renpy_vn_graph dialogue parsing helpers (no LLM calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipelines.renpy_vn_graph.fns import _parse_char_output, _split_dialogue, _MAX_LINE_CHARS


# ---------------------------------------------------------------------------
# _split_dialogue
# ---------------------------------------------------------------------------

def test_split_short_line_unchanged():
    assert _split_dialogue("Hello there.") == ["Hello there."]


def test_split_long_line_at_sentence():
    long = ("She turned to face him. " * 15).strip()
    boxes = _split_dialogue(long)
    assert len(boxes) > 1
    for box in boxes:
        assert len(box) <= _MAX_LINE_CHARS or " " not in box


def test_split_preserves_content():
    text = "First sentence. Second sentence. Third sentence."
    joined = " ".join(_split_dialogue(text))
    assert "First sentence" in joined
    assert "Third sentence" in joined


# ---------------------------------------------------------------------------
# _parse_char_output — plain speech
# ---------------------------------------------------------------------------

def test_plain_speech():
    lines, hist = _parse_char_output("Hello there, nice to meet you.", "elena")
    assert lines == ['    elena "Hello there, nice to meet you."']
    assert "Hello there" in hist


def test_strips_speaker_prefix():
    lines, hist = _parse_char_output("Elena: Hello there.", "elena")
    assert lines == ['    elena "Hello there."']


def test_strips_outer_quotes():
    # Outer quotes on input should not produce doubled quotes in output
    lines, hist = _parse_char_output('"Hello there."', "elena")
    assert lines == ['    elena "Hello there."']


def test_inner_quotes_become_single():
    lines, _ = _parse_char_output('He said "goodbye" to her.', "elena")
    assert '"goodbye"' not in lines[0]
    assert "'goodbye'" in lines[0]


# ---------------------------------------------------------------------------
# _parse_char_output — action segments
# ---------------------------------------------------------------------------

def test_action_becomes_narrator_line():
    lines, hist = _parse_char_output("*She smiles warmly.* I'm glad you came.", "elena")
    assert '    act "She smiles warmly."' in lines
    assert any("elena" in l for l in lines)


def test_action_at_end():
    lines, _ = _parse_char_output("I knew you'd come. *She turns away.*", "elena")
    assert any('act "She turns away."' in l for l in lines)
    assert any("elena" in l for l in lines)


def test_action_only():
    lines, hist = _parse_char_output("*She stands there in silence.*", "elena")
    assert lines == ['    act "She stands there in silence."']


def test_action_between_speech():
    lines, _ = _parse_char_output(
        "I've been waiting. *She looks away.* But not anymore.", "elena"
    )
    assert len(lines) == 3
    assert '    act "She looks away."' in lines
    assert lines[0].startswith("    elena")
    assert lines[2].startswith("    elena")


# ---------------------------------------------------------------------------
# _parse_char_output — tilde emphasis stripped to plain text
# ---------------------------------------------------------------------------

def test_tilde_stripped_to_plain():
    lines, _ = _parse_char_output("That was ~the last time~.", "elena")
    assert "{b}" not in lines[0]
    assert "the last time" in lines[0]
    assert lines[0].startswith("    elena")


def test_tilde_inline_plain():
    lines, _ = _parse_char_output("I loved him. That was ~everything~.", "elena")
    assert len(lines) == 1
    assert "everything" in lines[0]
    assert "{b}" not in lines[0]


# ---------------------------------------------------------------------------
# _parse_char_output — mixed: action kept, tilde stripped
# ---------------------------------------------------------------------------

def test_mixed_action_and_tilde():
    raw = "*She grabs his arm.* Don't go. ~Please.~"
    lines, hist = _parse_char_output(raw, "elena")
    assert any('act "She grabs his arm."' in l for l in lines)
    assert any("elena" in l for l in lines)
    assert not any("{b}" in l for l in lines)


def test_empty_input():
    lines, hist = _parse_char_output("", "elena")
    assert lines == []
    assert hist.strip() == ""
