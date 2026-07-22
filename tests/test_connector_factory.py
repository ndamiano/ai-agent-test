"""get_connector(): one connector, built once from the llm/workqueue settings."""

import pytest

import llm_clients.connector as mod
from llm_clients.connector import LLMConnector


@pytest.fixture(autouse=True)
def _clear_cache():
    mod._cached_connector = None
    yield
    mod._cached_connector = None


def _settings(monkeypatch, llm=None, workqueue=None):
    payload = {"llm": llm if llm is not None else {"model": "local-model"}}
    if workqueue is not None:
        payload["workqueue"] = workqueue
    monkeypatch.setattr(mod.settings_manager, "get_settings", lambda: payload)


def test_builds_from_llm_settings(monkeypatch):
    _settings(monkeypatch, llm={"model": "local-model", "max_tokens": 900,
                                "reasoning": "off"})
    conn = mod.get_connector()
    assert type(conn) is LLMConnector
    assert conn.model_name == "local-model"
    assert conn.max_tokens == 900
    assert conn.reasoning == "none"                     # on/off aliased to the effort enum
    assert conn.queue == "llm"


def test_job_timeout_comes_from_the_workqueue_block(monkeypatch):
    _settings(monkeypatch, workqueue={"enabled": True, "job_timeout_seconds": 42})
    assert mod.get_connector().job_timeout_seconds == 42


def test_singleton_reused(monkeypatch):
    _settings(monkeypatch)
    assert mod.get_connector() is mod.get_connector()
