"""OpenAI-compatible API connector"""

import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from config.settings_manager import settings_manager
from llm_clients.base_connector import BaseConnector
from llm_clients.log_context import get_log_dir
from llm_clients.rate_limiter import get_llm_rate_limiter

logger = logging.getLogger(__name__)


def _get_log_path(filename: str) -> str:
    log_dir = get_log_dir() or "logs"
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, filename)


def _write_log(log_file: str, entry: dict):
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry, indent=2))
            f.write("\n" + "="*80 + "\n")
    except Exception as e:
        logger.warning(f"Failed to log to {log_file}: {e}")


def _log_request_to_file(payload: dict, endpoint: str, request_id: str, metadata: dict = None):
    _write_log(_get_log_path("llm_requests.log"), {
        "request_id": request_id,
        "timestamp": datetime.now().isoformat(),
        "endpoint": endpoint,
        "payload": payload.get("input"),
        "metadata": metadata or {}
    })


def _log_response_to_file(response: Any, endpoint: str, request_id: str, metadata: dict = None):
    _write_log(_get_log_path("llm_responses.log"), {
        "request_id": request_id,
        "timestamp": datetime.now().isoformat(),
        "endpoint": endpoint,
        "response": response,
        "metadata": metadata or {}
    })


def _log_call(model: str, n_msgs: int, has_tools: bool, dt: float, result: Any) -> None:
    """One watchable INFO line per LLM call — so a 100-step run isn't a silent black box."""
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    tok = usage.get("completion_tokens", "?")
    logger.info("llm call: model=%s msgs=%d tools=%s %.1fs out_tok=%s",
                model, n_msgs, has_tools, dt, tok)


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


def _responses_stream_to_chat_chunks(events):
    """Responses-API SSE events -> chat-shaped streaming chunks.

    Re-emits the typed Responses event stream as OpenAI chat `{"choices":[{"delta":...}]}`
    chunks so existing accumulators (MainAgent._get_response_with_tools) work unchanged.

    Assistant text streams token-by-token via `response.output_text.delta`. Tool calls are
    taken from the terminal `response.output_item.done` item, which always carries the
    complete call (id, name, full arguments) — LM Studio sends function arguments as a single
    `.done` event, not incremental `.delta`s, so reconstructing from deltas drops the args.
    """
    n_tools = 0
    for ev in events:
        etype = ev.get("type")
        if etype == "response.created":
            resp = ev.get("response", {}) or {}
            yield {"id": resp.get("id"), "choices": []}
        elif etype == "response.output_text.delta":
            yield {"choices": [{"index": 0, "delta": {"content": ev.get("delta", "")}}]}
        elif etype == "response.output_item.done":
            item = ev.get("item", {}) or {}
            if item.get("type") == "function_call":
                tool_index = n_tools
                n_tools += 1
                yield {"choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": tool_index, "id": item.get("call_id"), "type": "function",
                    "function": {"name": item.get("name", ""),
                                 "arguments": item.get("arguments", "")}}]}}]}
        elif etype == "response.completed":
            resp = ev.get("response", {}) or {}
            u = resp.get("usage", {}) or {}
            od = u.get("output_tokens_details", {}) or {}
            finish = "tool_calls" if n_tools else "stop"
            yield {"usage": {"prompt_tokens": u.get("input_tokens"),
                             "completion_tokens": u.get("output_tokens"),
                             "total_tokens": u.get("total_tokens"),
                             "completion_tokens_details": {"reasoning_tokens": od.get("reasoning_tokens")}},
                   "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}


class OpenAICompatibleConnector(BaseConnector):
    connector_name = "openai_compatible"

    def __init__(self, base_url: str, api_key: Optional[str] = None, model: str = "default",
                 max_tokens: int = 50000, frequency_penalty: float = 0.5,
                 reasoning: Optional[str] = None):
        super().__init__()

        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model
        self.max_tokens = max_tokens
        self.frequency_penalty = frequency_penalty
        # Reasoning-effort for reasoning models, sent as reasoning.effort on the Responses API.
        # "none" disables reasoning — the lever that stops a local model burning 20-50k thinking
        # tokens per call; None lets the model pick. on/off aliased to the effort enum.
        self.reasoning = _resolve_effort(reasoning)
        self._response_format_supported = True
        self._streaming_works = True
        self._context_length: Optional[int] = None

        # The API root: a base_url already carrying a version segment ("/v1") is used as-is; a
        # bare host gets "/v1" appended. responses/models endpoints all derive from it.
        self._api_root = self.base_url if self._is_versioned_path(self.base_url) else f"{self.base_url}/v1"
        self.api_endpoint = f"{self._api_root}/responses"

    def get_context_length(self) -> Optional[int]:
        """Return the loaded model's context window size by querying /v1/models.

        Result is cached on the instance. Falls back to None if the endpoint
        is unreachable or doesn't return context_length.
        """
        if self._context_length is not None:
            return self._context_length
        try:
            response = self._get_session().get(
                f"{self._api_root}/models",
                headers=self._prepare_headers(),
                timeout=5,
            )
            if response.status_code == 200:
                models = response.json().get("data", [])
                match = next(
                    (m for m in models if m.get("id") == self.model_name),
                    models[0] if models else None,
                )
                if match:
                    ctx = match.get("context_length")
                    if ctx:
                        self._context_length = int(ctx)
                        return self._context_length
        except Exception:
            pass
        # The local router doesn't report context_length — fall back to the configured server window
        # (lmstudio.n_ctx, the launch `-c`). Without this the budget defaults to a value larger than
        # the real window and the transcript is never trimmed → the prompt overflows the context.
        try:
            n_ctx = (settings_manager.get_settings().get("lmstudio") or {}).get("n_ctx")
            if n_ctx:
                self._context_length = int(n_ctx)
                return self._context_length
        except Exception:
            pass
        return None

    def _is_versioned_path(self, url: str) -> bool:
        path = url.split('?')[0]
        parts = path.strip('/').split('/')
        last_part = parts[-1] if parts else ""
        return last_part.startswith('v') and last_part[1:].isdigit()

    def _prepare_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _unwrap_response(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if "data" in result and "choices" not in result:
            return result["data"]
        return result

    def generate_with_tools(self, messages: list, tools: list = None, response_format: dict = None,
                            max_tokens: int = None, reasoning=_REASONING_UNSET,
                            model: str = None, temperature: float = None) -> dict:
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            return {"error": error_msg}

        request_id = str(uuid.uuid4())
        return self._call_responses(messages, tools, response_format, max_tokens, request_id,
                                    reasoning, model=model, temperature=temperature)

    def _responses_payload(self, messages: list, tools: list, response_format: Optional[dict],
                           max_tokens: Optional[int], stream: bool,
                           reasoning=_REASONING_UNSET, model: str = None,
                           temperature: float = None) -> dict:
        """Build the Responses-API request body from chat-shaped inputs."""
        instructions, input_items = _chat_messages_to_responses_input(messages)
        payload = {
            "model": model or self.model_name,
            "input": input_items,
            "temperature": 0.7 if temperature is None else temperature,
            "max_output_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "stream": stream,
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

    def _call_responses(self, messages: list, tools: list, response_format: Optional[dict],
                        max_tokens: Optional[int], request_id: str,
                        reasoning=_REASONING_UNSET, model: str = None,
                        temperature: float = None) -> dict:
        """Call the OpenAI-compatible Responses endpoint, translating to/from chat shape.

        The Responses API honors reasoning.effort — how we keep a local reasoning model from
        spending ~30k tokens thinking per call. Speaks the same {choices, usage} shape back to
        callers as the old chat path did, so call sites are unchanged."""
        payload = self._responses_payload(messages, tools, response_format, max_tokens,
                                          stream=False, reasoning=reasoning, model=model,
                                          temperature=temperature)
        endpoint = self.api_endpoint
        _log_request_to_file(payload=payload, endpoint=endpoint, request_id=request_id,
                             metadata={"method": "responses", "model": self.model_name,
                                       "has_tools": bool(tools)})
        t0 = time.perf_counter()
        try:
            response = self._get_session().post(
                endpoint, json=payload, headers=self._prepare_headers(), timeout=300)
            if response.status_code == 200:
                raw = self._unwrap_response(response.json())
                result = _responses_to_chat(raw)
                _log_call(self.model_name, len(messages), bool(tools),
                          time.perf_counter() - t0, result)
                self._log_llm(messages, result)
                _log_response_to_file(raw, endpoint, request_id,
                                      {"method": "responses", "model": self.model_name})
                return result
            # Server doesn't understand text.format — drop it and retry once so structured
            # output degrades to free-form rather than failing the whole call.
            if response_format and "format" in response.text:
                logger.warning("Endpoint does not support text.format, retrying without it")
                self._response_format_supported = False
                payload.pop("text", None)
                response = self._get_session().post(
                    endpoint, json=payload, headers=self._prepare_headers(), timeout=300)
                if response.status_code == 200:
                    raw = self._unwrap_response(response.json())
                    result = _responses_to_chat(raw)
                    _log_call(self.model_name, len(messages), bool(tools),
                              time.perf_counter() - t0, result)
                    self._log_llm(messages, result)
                    _log_response_to_file(raw, endpoint, request_id,
                                          {"method": "responses", "model": self.model_name})
                    return result
            error_msg = f"Status {response.status_code}: {response.text}"
            self._log_llm(messages, None, error_msg)
            _log_response_to_file({"error": error_msg}, endpoint, request_id,
                                  {"method": "responses", "model": self.model_name})
            return {"error": error_msg}
        except requests.exceptions.RequestException as e:
            error_msg = f"Connection error: {str(e)}"
            self._log_llm(messages, None, error_msg)
            return {"error": error_msg}

    def generate_with_tools_stream(self, messages: list, tools: list = None):
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            yield {"error": error_msg}
            return

        request_id = str(uuid.uuid4())
        payload = self._responses_payload(messages, tools, None, None, stream=True)

        _log_request_to_file(
            payload=payload,
            endpoint=self.api_endpoint,
            request_id=request_id,
            metadata={"method": "generate_with_tools_stream", "model": self.model_name, "has_tools": bool(tools), "streaming": True}
        )

        try:
            response = self._get_session().post(
                self.api_endpoint,
                json=payload,
                headers=self._prepare_headers(),
                stream=True,
                timeout=(10, 300)
            )

            if response.status_code != 200:
                error_msg = f"Status {response.status_code}: {response.text}"
                logger.error(f"API streaming error: {error_msg}")
                self._log_llm(messages, None, error_msg)
                _log_response_to_file({"error": error_msg}, self.api_endpoint, request_id, {"method": "generate_with_tools_stream", "model": self.model_name})
                yield {"error": error_msg}
                return

            yield from _responses_stream_to_chat_chunks(self._iter_sse_events(response))

        except requests.exceptions.RequestException as e:
            error_msg = f"Streaming connection error: {str(e)}"
            logger.error(f"API streaming error: {error_msg}")
            self._log_llm(messages, None, error_msg)
            yield {"error": error_msg}

    def _iter_sse_events(self, response):
        """Parse an SSE stream into the JSON `data:` payloads (the typed Responses events)."""
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8')
            if not line.strip() or line.startswith(':') or not line.startswith('data: '):
                continue
            data_str = line[6:]
            if data_str.strip() == '[DONE]':
                break
            try:
                yield self._unwrap_response(json.loads(data_str))
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse streaming chunk: {e}")
                continue