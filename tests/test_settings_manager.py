import json
from pathlib import Path

from config.settings_manager import _merge, settings_manager

# Callers subscript get_settings() directly (db/store._db_path reads working_directory), so a key
# the file omits must resolve to its default, not raise KeyError three frames away.


def test_merge_fills_keys_the_file_omits():
    defaults = {"working_directory": "outputs", "llm": {"model": "m", "n_ctx": 32768}}
    merged = _merge(defaults, {"llm": {"model": "other"}})
    assert merged["working_directory"] == "outputs"
    assert merged["llm"] == {"model": "other", "n_ctx": 32768}   # block merged, not replaced


def test_merge_lets_the_file_win():
    merged = _merge({"a": 1, "b": {"c": 1}}, {"a": 2, "b": {"c": 2}})
    assert merged == {"a": 2, "b": {"c": 2}}


def test_example_settings_yield_every_key_callers_subscript():
    """CI bootstraps from settings.example.json; the example omits working_directory and every
    caller that subscripts it would KeyError."""
    example = json.loads(
        (Path(__file__).parent.parent / "src" / "config" / "settings.example.json").read_text())
    merged = _merge(settings_manager.defaults, example)
    assert merged["working_directory"]
    assert merged["llm"]["n_ctx"]

