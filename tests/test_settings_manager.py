"""Tests for SettingsManager validation with Pydantic schema"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.settings_manager import _merge, settings_manager
from config.settings_schema import AppSettings

# --- The loaded file layers OVER the defaults ---
#
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


def _valid():
    # Deep-copied per call: the negative rows below mutate nested keys in place.
    return copy.deepcopy(
        {
            "llm": {
                "model": "local-model",
                "max_tokens": 50000,
            },
        }
    )


# --- Positive contracts: each asserts a DISTINCT accepted-input guarantee. ---


def test_valid_settings_load():
    # A fully-specified settings dict loads and round-trips its fields.
    settings = AppSettings(**_valid())
    assert settings.llm.model == "local-model"
    assert settings.llm.max_tokens == 50000


def test_defaults_applied():
    # An omitted optional (max_tokens) falls back to the schema default, not an error.
    minimal = {
        "llm": {
            "model": "local-model",
        },
    }
    settings = AppSettings(**minimal)
    assert settings.llm.max_tokens == 50000


def test_optional_sections_absent():
    # Every section but llm is optional; absent, it stays None rather than failing.
    result = AppSettings(**_valid())
    assert result.comfyui is None
    assert result.workqueue is None


def test_extra_fields_ignored_by_default():
    # Unknown top-level keys are dropped, not surfaced as attributes or errors.
    settings = _valid()
    settings["unknown_field"] = "value"
    result = AppSettings(**settings)
    assert not hasattr(result, "unknown_field")


# --- Negative contract: any of these mutations to a valid dict must be rejected. ---


def _del(*path):
    def mutate(s):
        d = s
        for key in path[:-1]:
            d = d[key]
        del d[path[-1]]

    return mutate


def _set(value, *path):
    def mutate(s):
        d = s
        for key in path[:-1]:
            d = d[key]
        d[path[-1]] = value

    return mutate


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(_del("llm"), id="missing_llm_section"),
        pytest.param(_del("llm", "model"), id="missing_llm_model"),
        pytest.param(_set("", "llm", "model"), id="empty_llm_model"),
        pytest.param(_set(0, "llm", "max_tokens"), id="llm_max_tokens_zero"),
        pytest.param(_set(-1, "llm", "max_tokens"), id="llm_max_tokens_negative"),
    ],
)
def test_invalid_settings_rejected(mutation):
    # Required-field, enum, non-empty-string, and positive-int constraints must all
    # raise on a mutated-but-otherwise-valid dict; one row per constraint.
    settings = _valid()
    mutation(settings)
    with pytest.raises(ValidationError):
        AppSettings(**settings)
