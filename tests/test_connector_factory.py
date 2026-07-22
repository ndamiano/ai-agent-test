"""get_connector(): queue transport when workqueue.enabled, direct otherwise, with the cached
singleton invalidated by either half of that input."""

import pytest

import llm_clients.connector as mod
from llm_clients.openai_compatible_connector import OpenAICompatibleConnector
from llm_clients.queue_connector import QueueConnector


@pytest.fixture(autouse=True)
def _clear_cache():
    mod._cached_connector = None
    mod._cached_settings_hash = None
    yield
    mod._cached_connector = None
    mod._cached_settings_hash = None


def _settings(monkeypatch, llm=None, workqueue=None):
    payload = {
        "llm": llm if llm is not None else {
            "base_url": "http://localhost:1234", "model": "local-model"},
    }
    if workqueue is not None:
        payload["workqueue"] = workqueue
    monkeypatch.setattr(mod.settings_manager, "get_settings", lambda: payload)


def test_direct_transport_by_default(monkeypatch):
    _settings(monkeypatch)
    conn = mod.get_connector()
    assert type(conn) is OpenAICompatibleConnector
    assert conn.base_url == "http://localhost:1234"
    assert conn.model_name == "local-model"


def test_queue_transport_when_workqueue_enabled(monkeypatch):
    _settings(monkeypatch, workqueue={"enabled": True, "job_timeout_seconds": 42})
    conn = mod.get_connector()
    assert isinstance(conn, QueueConnector)
    assert conn.queue == "llm"
    assert conn.job_timeout_seconds == 42
    assert conn.model_name == "local-model"


def test_singleton_reused_for_identical_settings(monkeypatch):
    _settings(monkeypatch)
    assert mod.get_connector() is mod.get_connector()


def test_changed_model_rebuilds(monkeypatch):
    _settings(monkeypatch)
    first = mod.get_connector()
    _settings(monkeypatch, llm={"base_url": "http://localhost:1234", "model": "other"})
    assert mod.get_connector() is not first


def test_toggling_workqueue_rebuilds(monkeypatch):
    """workqueue lives outside the llm block the cache key is built from, so it has to be
    folded in explicitly."""
    _settings(monkeypatch, workqueue={"enabled": False})
    assert type(mod.get_connector()) is OpenAICompatibleConnector
    _settings(monkeypatch, workqueue={"enabled": True})
    assert isinstance(mod.get_connector(), QueueConnector)
