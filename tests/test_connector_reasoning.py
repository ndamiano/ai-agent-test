import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_clients.connector import LLMConnector


def _run(connector, monkeypatch, **kwargs):
    """Capture the Responses body the connector would enqueue."""
    captured = {}

    def _fake_job(body):
        captured.update(body)
        return {"status": "done", "result": {"id": "r", "output": [], "usage": {}}}

    monkeypatch.setattr(connector, "_run_job", _fake_job)
    connector.generate_with_tools([{"role": "user", "content": "hi"}], **kwargs)
    return captured


def test_responses_path_sends_reasoning_effort(monkeypatch):
    payload = _run(LLMConnector(model="m", reasoning="low"), monkeypatch)
    assert payload["reasoning"] == {"effort": "low"}
    assert "input" in payload and "messages" not in payload      # Responses shape


def test_responses_omits_reasoning_when_unset(monkeypatch):
    assert "reasoning" not in _run(LLMConnector(model="m"), monkeypatch)


def test_per_call_reasoning_overrides_configured(monkeypatch):
    # The executor escalates effort on a stalled target regardless of the configured floor.
    c = LLMConnector(model="m", reasoning="none")
    assert _run(c, monkeypatch, reasoning="high")["reasoning"] == {"effort": "high"}


def test_per_call_reasoning_aliases_resolve(monkeypatch):
    c = LLMConnector(model="m", reasoning="none")
    assert _run(c, monkeypatch, reasoning="on")["reasoning"] == {"effort": "high"}


def test_per_call_none_disables_configured_reasoning(monkeypatch):
    # "none" is the explicit OFF effort (the API rejects omitting it as "off"), so a per-call
    # "none" must send effort:none even when the connector is configured high.
    c = LLMConnector(model="m", reasoning="high")
    assert _run(c, monkeypatch, reasoning="none")["reasoning"] == {"effort": "none"}


def test_unset_per_call_falls_back_to_configured(monkeypatch):
    payload = _run(LLMConnector(model="m", reasoning="low"), monkeypatch)
    assert payload["reasoning"] == {"effort": "low"}
