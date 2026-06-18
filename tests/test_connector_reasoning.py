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


def test_chat_path_sends_reasoning_string(monkeypatch):
    # chat/completions honors a top-level `reasoning` string for models that support it
    # (Gemma 4: "none" disables thinking). Sent as a plain string, not the {effort} object.
    c = OpenAICompatibleConnector(base_url="http://x", model="m", reasoning="none", api_style="chat")
    payload = _run(c, {"choices": [{"message": {"content": "ok"}}], "usage": {}}, monkeypatch)
    assert payload["reasoning"] == "none"


def test_chat_path_omits_reasoning_when_unset(monkeypatch):
    c = OpenAICompatibleConnector(base_url="http://x", model="m", api_style="chat")
    payload = _run(c, {"choices": [{"message": {"content": "ok"}}], "usage": {}}, monkeypatch)
    assert "reasoning" not in payload


def test_responses_path_sends_reasoning_effort(monkeypatch):
    c = OpenAICompatibleConnector(base_url="http://x", model="m", reasoning="low",
                                  api_style="responses")
    body = {"id": "r", "output": [], "usage": {}}
    payload = _run(c, body, monkeypatch)
    assert payload["reasoning"] == {"effort": "low"}
    assert "input" in payload and "messages" not in payload      # Responses shape


def test_responses_omits_reasoning_when_unset(monkeypatch):
    c = OpenAICompatibleConnector(base_url="http://x", model="m", api_style="responses")
    payload = _run(c, {"id": "r", "output": [], "usage": {}}, monkeypatch)
    assert "reasoning" not in payload
