"""The worker translates a CANONICAL request for whatever its own target serves (`--api`)."""
import json
from unittest.mock import MagicMock

import pytest

from llm_clients import wire
from worker.agent import Agent
from worker.handlers import llm

CANONICAL = {
    "model": "m",
    "messages": [{"role": "system", "content": "you are X"},
                 {"role": "user", "content": "go"},
                 {"role": "assistant", "content": "", "tool_calls": [
                     {"id": "c1", "type": "function",
                      "function": {"name": "write_file", "arguments": '{"path":"a"}'}}]},
                 {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'}],
    "tools": [{"type": "function", "function": {"name": "write_file", "description": "d",
                                                "parameters": {"type": "object"}}}],
    "max_tokens": 900,
    "reasoning": "none",
}

CHAT_REPLY = {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
              "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
RESPONSES_REPLY = {"output": [{"type": "message",
                               "content": [{"type": "output_text", "text": "hi"}]}],
                   "usage": {"input_tokens": 5, "output_tokens": 2}}


def _agent(api, reply):
    a = Agent("http://server", "http://gpu", "llm", "tok", api=api, worker_id="w1")
    a.session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = reply
    a.session.post.return_value = resp
    return a


def test_chat_is_the_default_dialect():
    a = _agent("chat", CHAT_REPLY)
    result, err = llm(a, {"body": dict(CANONICAL)})
    assert err is None
    url = a.session.post.call_args[0][0]
    assert url == "http://gpu/v1/chat/completions"
    assert result == CHAT_REPLY                       # already canonical, no translation back


def test_chat_passes_messages_and_tools_through_untranslated():
    a = _agent("chat", CHAT_REPLY)
    llm(a, {"body": dict(CANONICAL)})
    sent = a.session.post.call_args[1]["json"]
    assert sent["messages"] == CANONICAL["messages"]
    assert sent["tools"] == CANONICAL["tools"]
    assert "reasoning" not in sent                    # meaningless on this endpoint
    assert sent["enable_thinking"] is False           # the template switch is what disables it
    assert sent["presence_penalty"] == 0              # never inherit ninfer's 1.0 default


def test_responses_dialect_translates_both_ways():
    a = _agent("responses", RESPONSES_REPLY)
    result, err = llm(a, {"body": dict(CANONICAL)})
    assert err is None
    assert a.session.post.call_args[0][0] == "http://gpu/v1/responses"
    sent = a.session.post.call_args[1]["json"]
    assert sent["instructions"] == "you are X"        # system -> instructions
    assert [i["type"] for i in sent["input"]] == ["message", "function_call",
                                                  "function_call_output"]
    assert sent["tools"][0]["name"] == "write_file"   # flattened, no `function` nesting
    assert sent["max_output_tokens"] == 900
    # ...and the reply comes back canonical, so the control plane never sees the dialect
    assert result["choices"][0]["message"]["content"] == "hi"
    assert result["usage"]["prompt_tokens"] == 5


def test_an_upstream_error_is_reported_not_raised():
    a = _agent("chat", CHAT_REPLY)
    a.session.post.return_value = MagicMock(status_code=500, text="boom")
    result, err = llm(a, {"body": dict(CANONICAL)})
    assert result is None and "500" in err


def test_the_canonical_body_is_never_mutated():
    """The same dict is the durable job payload; translating must not rewrite it."""
    body = dict(CANONICAL)
    before = json.dumps(body, sort_keys=True)
    llm(_agent("responses", RESPONSES_REPLY), {"body": body})
    assert json.dumps(body, sort_keys=True) == before


@pytest.mark.parametrize("effort,expected", [("none", True), ("high", False)])
def test_reasoning_none_is_the_template_switch_on_both_dialects(effort, expected):
    body = {**CANONICAL, "reasoning": effort}
    for api in ("chat", "responses"):
        a = _agent(api, CHAT_REPLY if api == "chat" else RESPONSES_REPLY)
        llm(a, {"body": dict(body)})
        sent = a.session.post.call_args[1]["json"]
        assert ("chat_template_kwargs" in sent) is expected
