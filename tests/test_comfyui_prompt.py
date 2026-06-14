import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.comfyui_tools import _build_character_prompt


def test_handles_string_tags():
    pos, _ = _build_character_prompt({"appearance_tags": {
        "body": "tall", "hair": "black bob", "eyes": "brown",
        "clothing": "apron", "distinguishing": "flour-dusted hands",
    }})
    assert "tall" in pos and "apron" in pos


def test_coerces_list_valued_tag():
    # the model sometimes emits a list instead of a string — must not crash
    pos, _ = _build_character_prompt({"appearance_tags": {
        "body": "tall", "clothing": ["blazer", "loose tie"],
    }})
    assert "blazer, loose tie" in pos


def test_skips_missing_and_empty_tags():
    pos, _ = _build_character_prompt({"appearance_tags": {"hair": "red", "clothing": ""}})
    assert "red" in pos
    assert ", , " not in pos  # no empty slots leaking into the join


def test_missing_appearance_tags_entirely():
    pos, neg = _build_character_prompt({})
    assert isinstance(pos, str) and isinstance(neg, str)
