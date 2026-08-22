"""LLM calls for worldgen, routed through maestro's `llm` queue.

worldgen was written against `ai_harness` — `LLMHarness`, `Message`, `@tool` —
which ran its own tool loop against a provider it dialed directly. Maestro has
one path to an inference server: `llm_clients.connector.get_connector()`. This
module reproduces the ai_harness call surface worldgen uses so call sites
needed only their harness-construction lines changed, and runs the tool loop
here instead, against the queue.

`endpoint` is gone: the queue's worker decides which server answers a call, so
a per-harness endpoint would be a parameter nothing reads.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from config.settings_manager import settings_manager
from llm_clients.connector import LLMConnector

from . import WORLD_JOB_TIMEOUT
from .llm_tools import Tool, as_tool, tool

MAX_TOOL_ROUNDS = 10

_world_connector: LLMConnector | None = None


def _get_connector() -> LLMConnector:
    """A worldgen-owned connector, never the shared `get_connector()` singleton: that one is
    cached with `workqueue.job_timeout_seconds` (900s), too short for a job waiting behind a
    model swap plus another queue draining."""
    global _world_connector
    if _world_connector is None:
        llm = settings_manager.get_settings().get("llm") or {}
        _world_connector = LLMConnector(
            model=llm.get("model", "default"),
            # No output cap: a worldgen prompt is a transcript plus several renders, and a cap
            # that is most of the window leaves the input the smaller half of it.
            max_tokens=None,
            reasoning=llm.get("reasoning"),
            job_timeout_seconds=WORLD_JOB_TIMEOUT)
    return _world_connector


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str
    content: Any = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class Response:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # convenience: str(resp) == resp.text
        return self.text


def _to_response(result: dict) -> Response:
    if "error" in result and "choices" not in result:
        raise RuntimeError(f"llm queue call failed: {result['error']}")
    message = result["choices"][0]["message"]
    tool_calls = [
        ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=json.loads(tc["function"]["arguments"] or "{}"),
        )
        for tc in message.get("tool_calls") or []
    ]
    return Response(text=message.get("content") or "", tool_calls=tool_calls, raw=result)


class LLMHarness:
    """Minimal-overhead LLM client over the maestro queue.

        h = LLMHarness(tools=[get_weather], system="...")
        h.send_message("hello")
        h.send_message_with_tools("weather in Paris?")

    Args:
        tools: default tools available to every *_with_tools call.
        system: default system prompt.
        model: forwarded to the connector; omit to use the configured default.
        temperature: sampling temperature for every call. Each worldgen stage sets its
            own — the grounding pass is greedy, the planners are not — so a harness that
            dropped it would run every stage at the connector's one default.
        max_tool_rounds: cap on tool-loop rounds before the loop stops asking.
    """

    def __init__(
        self,
        tools: Sequence[Any] | None = None,
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        **_ignored: Any,
    ) -> None:
        self.system = system
        self.model = model
        self.temperature = temperature
        self.max_tool_rounds = max_tool_rounds
        self.tools: list[Tool] = [as_tool(t) for t in (tools or [])]
        self.history: list[Message] = []

    # -- helpers ---------------------------------------------------------
    def _build(
        self, prompt: str | list[Message] | None, system: str | None, history: bool
    ) -> list[Message]:
        msgs: list[Message] = []
        sys_prompt = system if system is not None else self.system
        if sys_prompt:
            msgs.append(Message("system", sys_prompt))
        if history:
            msgs += self.history
        if isinstance(prompt, str):
            msgs.append(Message("user", prompt))
        elif prompt:
            msgs += prompt
        return msgs

    def _resolve_tools(self, tools: Sequence[Any] | None) -> list[Tool]:
        return [as_tool(t) for t in tools] if tools is not None else self.tools

    @staticmethod
    def _run_tool(tool_: Tool, call: ToolCall) -> str:
        if tool_.fn is None:
            return f"error: tool {call.name} has no implementation bound"
        try:
            result = tool_.fn(**tool_.bind(call.arguments))
        except Exception as e:  # surface failures to the model, don't crash the loop
            return f"error: {type(e).__name__}: {e}"
        return result if isinstance(result, str) else json.dumps(result, default=str)

    # -- public API ------------------------------------------------------
    def send_message(
        self,
        prompt: str | list[Message] | None = None,
        *,
        system: str | None = None,
        history: bool = False,
        **_ignored: Any,
    ) -> Response:
        """One round trip, no tools. Returns a Response (str(resp) == text)."""
        msgs = self._build(prompt, system, history)
        result = _get_connector().generate_with_tools(
            [m.to_dict() for m in msgs], reasoning="none", model=self.model,
            temperature=self.temperature,
        )
        resp = _to_response(result)
        if history:
            self.history.append(Message("user", prompt if isinstance(prompt, str) else ""))
            self.history.append(Message("assistant", resp.text))
        return resp

    def send_message_with_tools(
        self,
        prompt: str | list[Message] | None = None,
        *,
        tools: Sequence[Any] | None = None,
        system: str | None = None,
        history: bool = False,
        max_rounds: int | None = None,
        **_ignored: Any,
    ) -> Response:
        """Run the tool loop until the model answers without calling a tool.

        `response.raw["messages"]` holds the full transcript of the loop.
        """
        tool_list = self._resolve_tools(tools)
        by_name = {t.name: t for t in tool_list}
        schema = [t.to_json_schema() for t in tool_list]
        msgs = self._build(prompt, system, history)
        rounds = max_rounds if max_rounds is not None else self.max_tool_rounds
        connector = _get_connector()

        resp = _to_response(connector.generate_with_tools(
            [m.to_dict() for m in msgs], tools=schema, reasoning="none", model=self.model,
            temperature=self.temperature,
        ))
        for _ in range(rounds):
            if not resp.tool_calls:
                break
            msgs.append(Message("assistant", resp.text or None, tool_calls=resp.tool_calls))
            for call in resp.tool_calls:
                t = by_name.get(call.name)
                out = (
                    f"error: unknown tool {call.name}"
                    if t is None
                    else self._run_tool(t, call)
                )
                msgs.append(Message("tool", out, tool_call_id=call.id, name=call.name))
            resp = _to_response(connector.generate_with_tools(
                [m.to_dict() for m in msgs], tools=schema, reasoning="none", model=self.model,
                temperature=self.temperature,
            ))

        msgs.append(Message("assistant", resp.text))
        resp.raw = {**resp.raw, "messages": msgs}
        if history:
            self.history = [m for m in msgs if m.role != "system"]
        return resp

    def reset(self) -> None:
        """Clear conversation history."""
        self.history.clear()


__all__ = ["LLMHarness", "Message", "Response", "ToolCall", "tool", "as_tool", "Tool"]
