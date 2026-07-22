import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agents.main_agent import MainAgent
from tools.execution_context import get_user_id


class _FakeStreamConnector:
    """Fakes the connector's chat-shaped streaming chunks (one batch per LLM call)."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.calls = 0

    def generate_with_tools_stream(self, messages, tools):
        batch = self._batches[self.calls]
        self.calls += 1
        yield from batch

    def generate_with_tools(self, messages, tools):
        raise AssertionError("non-streaming fallback should not be used in this test")


def _make_agent(connector):
    agent = MainAgent(agent_id="chat")
    agent.connector = connector
    return agent


def test_chat_stream_forwards_tokens_and_tool_progress_then_done(monkeypatch):
    first_call = [
        {"id": "r1", "choices": [{"index": 0, "delta": {"content": "Drafting"}}]},
        {"choices": [{"index": 0, "delta": {"content": " it"}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": "call1", "type": "function",
            "function": {"name": "propose_game_spec", "arguments": "{\"request\": \"a cozy game\"}"},
        }]}, "finish_reason": "tool_calls"}]},
    ]
    second_call = [
        {"choices": [{"index": 0, "delta": {"content": "Here is your spec!"}, "finish_reason": "stop"}]},
    ]
    connector = _FakeStreamConnector([first_call, second_call])
    agent = _make_agent(connector)

    tool_calls_seen = []
    monkeypatch.setattr(
        "agents.main_agent.tool_manager.useTool",
        lambda name, **kwargs: tool_calls_seen.append((name, kwargs)) or {"run_id": "abc123"},
    )

    events = list(agent.chat_stream("make me a cozy game"))

    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events[:2]) == "Drafting it"

    tool_events = [e for e in events if e["type"] == "tool"]
    assert tool_events == [
        {"type": "tool", "tool_name": "propose_game_spec", "status": "start"},
        {"type": "tool", "tool_name": "propose_game_spec", "status": "success"},
    ]

    assert tool_calls_seen == [("propose_game_spec", {"request": "a cozy game"})]

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["message"] == "Here is your spec!"

    # History carries both assistant turns + the tool result so a follow-up call is coherent.
    roles = [m["role"] for m in agent.message_history]
    assert roles == ["user", "assistant", "tool", "assistant"]


def test_tool_runs_under_the_agents_user_context(monkeypatch):
    """The run-creating tool reads the authed user via get_user_id(); the agent must
    re-establish it around the tool call (the contextvar does not survive the streaming
    boundary, so binding it on the agent is what makes ownership attribution work)."""
    first_call = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": "call1", "type": "function",
            "function": {"name": "propose_game_spec", "arguments": "{}"},
        }]}, "finish_reason": "tool_calls"}]},
    ]
    second_call = [
        {"choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]},
    ]
    agent = MainAgent(agent_id="chat", user_id="u42")
    agent.connector = _FakeStreamConnector([first_call, second_call])

    seen = {}

    def _record(name, **kwargs):
        seen["user_id"] = get_user_id()
        return {"ok": True}

    monkeypatch.setattr("agents.main_agent.tool_manager.useTool", _record)

    list(agent.chat_stream("make me a game"))
    assert seen["user_id"] == "u42"


def test_chat_stream_surfaces_tool_failure(monkeypatch):
    first_call = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": "call1", "type": "function",
            "function": {"name": "propose_game_spec", "arguments": "{}"},
        }]}, "finish_reason": "tool_calls"}]},
    ]
    second_call = [
        {"choices": [{"index": 0, "delta": {"content": "Let me try again."}, "finish_reason": "stop"}]},
    ]
    connector = _FakeStreamConnector([first_call, second_call])
    agent = _make_agent(connector)

    def _boom(name, **kwargs):
        raise RuntimeError("spec drafting blew up")

    monkeypatch.setattr("agents.main_agent.tool_manager.useTool", _boom)

    events = list(agent.chat_stream("make me a game"))
    tool_events = [e for e in events if e["type"] == "tool"]
    assert tool_events == [
        {"type": "tool", "tool_name": "propose_game_spec", "status": "start"},
        {"type": "tool", "tool_name": "propose_game_spec", "status": "failed"},
    ]
    assert any("failed" in m["content"] for m in agent.message_history if m["role"] == "tool")


def test_chat_stream_error_event_on_connector_error():
    connector = _FakeStreamConnector([[{"error": "connection refused"}]])
    connector.generate_with_tools = lambda messages, tools: {"error": "connection refused"}
    agent = _make_agent(connector)

    events = list(agent.chat_stream("hello"))
    assert events[-1] == {"type": "error", "message": "Connector error: connection refused"}


def test_chat_non_streaming_wrapper_returns_final_text(monkeypatch):
    connector = _FakeStreamConnector([
        [{"choices": [{"index": 0, "delta": {"content": "Hi there"}, "finish_reason": "stop"}]}],
    ])
    agent = _make_agent(connector)

    reply = agent.chat("hello")
    assert reply == "Hi there"
