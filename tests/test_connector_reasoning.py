import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_clients.openai_compatible_connector import OpenAICompatibleConnector


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Session:
    def __init__(self, body):
        self.captured = None
        self._body = body

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.captured = json
        return _Resp(self._body)


def _run(connector, body, monkeypatch):
    sess = _Session(body)
    monkeypatch.setattr(connector, "_get_session", lambda: sess)
    connector.generate_with_tools([{"role": "user", "content": "hi"}])
    return sess.captured


def test_responses_path_sends_reasoning_effort(monkeypatch):
    c = OpenAICompatibleConnector(base_url="http://x", model="m", reasoning="low")
    body = {"id": "r", "output": [], "usage": {}}
    payload = _run(c, body, monkeypatch)
    assert payload["reasoning"] == {"effort": "low"}
    assert "input" in payload and "messages" not in payload      # Responses shape


def test_responses_omits_reasoning_when_unset(monkeypatch):
    c = OpenAICompatibleConnector(base_url="http://x", model="m")
    payload = _run(c, {"id": "r", "output": [], "usage": {}}, monkeypatch)
    assert "reasoning" not in payload


def _run_with_reasoning(connector, reasoning, monkeypatch):
    sess = _Session({"id": "r", "output": [], "usage": {}})
    monkeypatch.setattr(connector, "_get_session", lambda: sess)
    connector.generate_with_tools([{"role": "user", "content": "hi"}], reasoning=reasoning)
    return sess.captured


def test_per_call_reasoning_overrides_configured(monkeypatch):
    # The executor escalates effort on a stalled target regardless of the configured floor.
    c = OpenAICompatibleConnector(base_url="http://x", model="m", reasoning="none")
    payload = _run_with_reasoning(c, "high", monkeypatch)
    assert payload["reasoning"] == {"effort": "high"}


def test_per_call_reasoning_aliases_resolve(monkeypatch):
    c = OpenAICompatibleConnector(base_url="http://x", model="m", reasoning="none")
    assert _run_with_reasoning(c, "on", monkeypatch)["reasoning"] == {"effort": "high"}


def test_per_call_none_disables_configured_reasoning(monkeypatch):
    # "none" is the explicit OFF effort (the API rejects omitting it as "off"), so a per-call
    # "none" must send effort:none even when the connector is configured high.
    c = OpenAICompatibleConnector(base_url="http://x", model="m", reasoning="high")
    assert _run_with_reasoning(c, "none", monkeypatch)["reasoning"] == {"effort": "none"}


def test_unset_per_call_falls_back_to_configured(monkeypatch):
    c = OpenAICompatibleConnector(base_url="http://x", model="m", reasoning="low")
    payload = _run(c, {"id": "r", "output": [], "usage": {}}, monkeypatch)
    assert payload["reasoning"] == {"effort": "low"}
