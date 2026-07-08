"""Tests for SettingsManager validation with Pydantic schema"""

import copy

import pytest
from config.settings_schema import AppSettings
from pydantic import ValidationError


def _valid():
    # Fresh, deep-copied valid settings per call so a row mutating a nested
    # section (lmstudio/cline) can't bleed into another row.
    return copy.deepcopy(
        {
            "connector_type": "lmstudio",
            "lmstudio": {
                "base_url": "http://localhost:1234",
                "model": "local-model",
                "max_tokens": 50000,
            },
            "cline": {
                "api_key": "",
                "model": "claude-sonnet-4-5",
                "max_tokens": 50000,
            },
        }
    )


# --- Positive contracts: each asserts a DISTINCT accepted-input guarantee. ---


def test_valid_settings_load():
    # A fully-specified settings dict loads and round-trips its fields.
    settings = AppSettings(**_valid())
    assert settings.connector_type == "lmstudio"
    assert settings.lmstudio.base_url == "http://localhost:1234"
    assert settings.cline.model == "claude-sonnet-4-5"


def test_defaults_applied():
    # An omitted optional (max_tokens) falls back to the schema default, not an error.
    minimal = {
        "connector_type": "lmstudio",
        "lmstudio": {
            "base_url": "http://localhost:1234",
            "model": "local-model",
        },
    }
    settings = AppSettings(**minimal)
    assert settings.lmstudio.max_tokens == 50000


def test_cline_optional():
    # The whole cline section is optional; absent it stays None rather than failing.
    settings = {
        "connector_type": "lmstudio",
        "lmstudio": {
            "base_url": "http://localhost:1234",
            "model": "local-model",
        },
    }
    result = AppSettings(**settings)
    assert result.cline is None


def test_cline_connector_type():
    # "cline" is an accepted connector_type value alongside "lmstudio".
    settings = _valid()
    settings["connector_type"] = "cline"
    result = AppSettings(**settings)
    assert result.connector_type == "cline"


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
        pytest.param(_del("connector_type"), id="missing_connector_type"),
        pytest.param(_set("invalid", "connector_type"), id="invalid_connector_type"),
        pytest.param(_del("lmstudio"), id="missing_lmstudio_section"),
        pytest.param(_del("lmstudio", "base_url"), id="missing_lmstudio_base_url"),
        pytest.param(_del("lmstudio", "model"), id="missing_lmstudio_model"),
        pytest.param(_set("", "lmstudio", "base_url"), id="empty_lmstudio_base_url"),
        pytest.param(_set("", "lmstudio", "model"), id="empty_lmstudio_model"),
        pytest.param(_set(0, "lmstudio", "max_tokens"), id="lmstudio_max_tokens_zero"),
        pytest.param(_set(-1, "lmstudio", "max_tokens"), id="lmstudio_max_tokens_negative"),
        pytest.param(_del("cline", "api_key"), id="missing_cline_api_key"),
        pytest.param(_del("cline", "model"), id="missing_cline_model"),
        pytest.param(_set("", "cline", "model"), id="empty_cline_model"),
        pytest.param(_set(0, "cline", "max_tokens"), id="cline_max_tokens_zero"),
    ],
)
def test_invalid_settings_rejected(mutation):
    # Required-field, enum, non-empty-string, and positive-int constraints must all
    # raise on a mutated-but-otherwise-valid dict; one row per constraint.
    settings = _valid()
    mutation(settings)
    with pytest.raises(ValidationError):
        AppSettings(**settings)
