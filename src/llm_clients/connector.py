"""LLM inference: chat-shaped calls in, Responses-API payloads out, executed by a worker agent.

Callers speak the OpenAI chat shape (messages/tools, {choices, usage} back). The wire format is
the Responses API (/v1/responses) — the one local endpoint that honors reasoning.effort. The
payload lands as a jobs row and a worker forwards it verbatim to ITS local server, so model
routing lives with the GPU, not here.
"""

import logging
import time
import uuid
from typing import Optional

from config.settings_manager import settings_manager
from db import queue_client
from llm_clients.rate_limiter import get_llm_rate_limiter

logger = logging.getLogger(__name__)


def _log_call(model: str, n_msgs: int, has_tools: bool, dt: float, result) -> None:
    """One watchable INFO line per LLM call — so a 100-step run isn't a silent black box."""
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    logger.info("llm call: model=%s msgs=%d tools=%s %.1fs out_tok=%s",
                model, n_msgs, has_tools, dt, usage.get("completion_tokens", "?"))


# Valid `reasoning.effort` values for the Responses API (per LM Studio's enum). "off" is NOT
# valid — it errors; use "none" to disable reasoning entirely.
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
# LM Studio's NATIVE chat API uses on/off; people reach for those here too. Map them to the
# Responses effort enum so a setting of "on"/"off" works instead of being silently dropped
# (a dropped value = no reasoning field = the model reasons unbounded again).
_REASONING_ALIASES = {"on": "high", "off": "none"}

# Sentinel: caller did not pass a per-call reasoning override, so fall back to self.reasoning.
# Distinct from None/"none" which are explicit values ("none" = reasoning off).
_REASONING_UNSET = object()


def _resolve_effort(value):
    """Map a reasoning value (incl. on/off aliases) to the Responses effort enum, or None."""
    if isinstance(value, str):
        value = _REASONING_ALIASES.get(value, value)
    return value


def _chat_tools_to_responses(tools):
    """OpenAI chat tool schema -> Responses tool schema (flat: no `function` nesting)."""
    out = []
    for t in tools or []:
        fn = t.get("function", t)
        out.append({"type": "function", "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {})})
    return out


def _chat_messages_to_responses_input(messages):
    """OpenAI chat messages[] -> (instructions, Responses input[]).

    system -> instructions; assistant tool_calls -> function_call items; role:tool results ->
    function_call_output items; plain text -> role+content items. Stateless: the whole history
    is re-sent each call, matching how the executor rebuilds context (no previous_response_id).
    """
    instructions = None
    items = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            instructions = f"{instructions}\n\n{content}" if instructions else (content or "")
        elif role == "tool":
            items.append({"type": "function_call_output",
                          "call_id": m.get("tool_call_id"), "output": content or ""})
        elif role == "assistant":
            if content:
                items.append({"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": content}]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                items.append({"type": "function_call", "call_id": tc.get("id"),
                              "name": fn.get("name"), "arguments": fn.get("arguments", "")})
        else:  # user / other
            items.append({"type": "message", "role": role or "user", "content": content or ""})
    return instructions, items


def _responses_to_chat(resp):
    """Responses output[] -> OpenAI chat {choices:[{message}], usage} so callers are unchanged."""
    text_parts, tool_calls = [], []
    for o in resp.get("output", []) or []:
        t = o.get("type")
        if t == "message":
            for c in o.get("content", []) or []:
                if c.get("type") == "output_text":
                    text_parts.append(c.get("text", ""))
        elif t == "function_call":
            tool_calls.append({"id": o.get("call_id"), "type": "function",
                               "function": {"name": o.get("name"),
                                            "arguments": o.get("arguments", "")}})
    msg = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    u = resp.get("usage", {}) or {}
    od = u.get("output_tokens_details", {}) or {}
    usage = {"prompt_tokens": u.get("input_tokens"),
             "completion_tokens": u.get("output_tokens"),
             "total_tokens": u.get("total_tokens"),
             "completion_tokens_details": {"reasoning_tokens": od.get("reasoning_tokens")}}
    return {"choices": [{"message": msg}], "usage": usage, "id": resp.get("id")}


class LLMConnector:
    def __init__(self, model: str = "default", max_tokens: int = 50000,
                 frequency_penalty: float = 0.5, reasoning: Optional[str] = None,
                 queue: str = "llm", job_timeout_seconds: float = 900):
        self.model_name = model
        self.max_tokens = max_tokens
        self.frequency_penalty = frequency_penalty
        # Reasoning-effort for reasoning models, sent as reasoning.effort. "none" disables
        # reasoning; None lets the model pick. on/off aliased to the effort enum.
        self.reasoning = _resolve_effort(reasoning)
        self.queue = queue
        self.job_timeout_seconds = job_timeout_seconds
        self._response_format_supported = True
        self._context_length: Optional[int] = None

    def get_context_length(self) -> Optional[int]:
        """No server to ask — the configured window (llm.n_ctx) is the answer."""
        if self._context_length is None:
            n_ctx = (settings_manager.get_settings().get("llm") or {}).get("n_ctx")
            self._context_length = int(n_ctx) if n_ctx else None
        return self._context_length

    def generate_with_tools(self, messages: list, tools: list = None,
                            response_format: dict = None, max_tokens: int = None,
                            reasoning=_REASONING_UNSET, model: str = None) -> dict:
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            return {"error": error_msg}

        payload = self._responses_payload(messages, tools, response_format, max_tokens,
                                          reasoning=reasoning, model=model)
        t0 = time.perf_counter()
        job = self._run_job(payload)
        if job["status"] == "done":
            result = _responses_to_chat(job["result"])
            _log_call(payload["model"], len(messages), bool(tools),
                      time.perf_counter() - t0, result)
            return result
        # Worker reported an upstream error. A server that rejects text.format degrades to
        # free-form rather than failing the whole call.
        error = job.get("error") or "job lost"
        if response_format and "format" in error:
            logger.warning("Endpoint does not support text.format, retrying without it")
            self._response_format_supported = False
            payload.pop("text", None)
            job = self._run_job(payload)
            if job["status"] == "done":
                return _responses_to_chat(job["result"])
            error = job.get("error") or "job lost"
        logger.error("LLM call (%s) failed: %s", self.model_name, error)
        return {"error": error}

    def generate_with_tools_stream(self, messages: list, tools: list = None):
        """The queue is request/response; streaming callers get the full reply as one chunk."""
        result = self.generate_with_tools(messages, tools)
        if "error" in result:
            yield {"error": result["error"]}
            return
        msg = result["choices"][0]["message"]
        yield {"id": f"queue-{uuid.uuid4().hex[:8]}", "choices": []}
        if msg.get("content"):
            yield {"choices": [{"index": 0, "delta": {"content": msg["content"]}}]}
        for i, tc in enumerate(msg.get("tool_calls") or []):
            yield {"choices": [{"index": 0, "delta": {"tool_calls": [{**tc, "index": i}]}}]}
        finish = "tool_calls" if msg.get("tool_calls") else "stop"
        yield {"usage": result.get("usage", {}),
               "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}

    def _responses_payload(self, messages: list, tools: list, response_format: Optional[dict],
                           max_tokens: Optional[int], reasoning=_REASONING_UNSET,
                           model: str = None) -> dict:
        """Build the Responses-API request body from chat-shaped inputs."""
        instructions, input_items = _chat_messages_to_responses_input(messages)
        payload = {
            "model": model or self.model_name,
            "input": input_items,
            "temperature": 0.7,
            "max_output_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "stream": False,
        }
        if instructions:
            payload["instructions"] = instructions
        # Per-call override (the executor escalates effort when a target stalls) falls back to
        # the connector's configured effort when unset.
        effort = self.reasoning if reasoning is _REASONING_UNSET else _resolve_effort(reasoning)
        if effort in _REASONING_EFFORTS:
            payload["reasoning"] = {"effort": effort}
        if effort == "none":
            # llama.cpp ignores reasoning.effort "none" — the model thinks anyway and can burn
            # the whole output budget before any text. The chat-template switch actually
            # disables it (verified: Qwen3.6 output drops from 2500 truncated to instant text);
            # servers that don't know the field ignore it.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            payload["tools"] = _chat_tools_to_responses(tools)
            payload["tool_choice"] = "auto"
        # JSON / structured output lives under text.format on the Responses API, not the
        # chat/completions top-level response_format field.
        if response_format and self._response_format_supported:
            payload["text"] = {"format": response_format}
        return payload

    def _run_job(self, payload: dict) -> dict:
        """Enqueue one Responses request and wait for a worker to land it. Returns the job row;
        status 'failed' with an error on timeout, so callers have one shape to branch on."""
        return queue_client.run_job(
            self.queue, {"path": "/v1/responses", "body": payload},
            model=payload.get("model"), timeout_seconds=self.job_timeout_seconds)


_cached_connector: Optional[LLMConnector] = None


def get_connector() -> LLMConnector:
    global _cached_connector
    if _cached_connector is None:
        settings = settings_manager.get_settings()
        llm = settings.get("llm") or {}
        workqueue = settings.get("workqueue") or {}
        _cached_connector = LLMConnector(
            model=llm.get("model", "default"),
            max_tokens=llm.get("max_tokens", 50000),
            frequency_penalty=llm.get("frequency_penalty", 0.5),
            reasoning=llm.get("reasoning"),
            job_timeout_seconds=workqueue.get("job_timeout_seconds", 900))
    return _cached_connector
