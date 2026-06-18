"""OpenAI-compatible API connector"""

import logging
import requests
import json
import time
import uuid
from typing import Any, Dict, Optional
from datetime import datetime
from llm_clients.base_connector import BaseConnector
from llm_clients.rate_limiter import get_llm_rate_limiter

logger = logging.getLogger(__name__)


def _get_log_path(filename: str) -> str:
    from llm_clients.log_context import get_log_dir
    import os
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
        # chat/completions carries "messages"; the Responses API carries "input".
        "payload": payload.get("messages", payload.get("input")),
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
                items.append({"role": "assistant",
                              "content": [{"type": "output_text", "text": content}]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                items.append({"type": "function_call", "call_id": tc.get("id"),
                              "name": fn.get("name"), "arguments": fn.get("arguments", "")})
        else:  # user / other
            items.append({"role": role or "user", "content": content or ""})
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


class OpenAICompatibleConnector(BaseConnector):
    connector_name = "openai_compatible"

    def __init__(self, base_url: str, api_key: Optional[str] = None, model: str = "default",
                 max_tokens: int = 50000, frequency_penalty: float = 0.5,
                 reasoning: Optional[str] = None, api_style: str = "chat"):
        super().__init__()

        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model
        self.max_tokens = max_tokens
        self.frequency_penalty = frequency_penalty
        # Reasoning-effort for reasoning models. On the Responses API (api_style="responses")
        # this becomes reasoning.effort and is the ONLY place it actually takes effect — the
        # chat/completions endpoint silently ignores it, letting the model burn 20-50k thinking
        # tokens per call. "none" disables reasoning; None lets the model pick. on/off aliased.
        self.reasoning = _REASONING_ALIASES.get(reasoning, reasoning) if isinstance(reasoning, str) else reasoning
        # "chat" → /chat/completions (OpenAI standard); "responses" → /v1/responses, the only
        # OpenAI-compatible LM Studio path that honors reasoning effort.
        self.api_style = api_style
        self._response_format_supported = True
        # The Responses path has no SSE translation here, so don't let callers attempt
        # streaming (they'd fail and fall back every call, logging a warning each time).
        self._streaming_works = api_style != "responses"
        self._context_length: Optional[int] = None

        # The API root: a base_url already carrying a version segment ("/v1") is used as-is; a
        # bare host gets "/v1" appended. chat/responses/models endpoints all derive from it.
        self._api_root = self.base_url if self._is_versioned_path(self.base_url) else f"{self.base_url}/v1"
        self.api_endpoint = f"{self._api_root}/chat/completions"

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

    def generate_with_tools(self, messages: list, tools: list = None, response_format: dict = None, max_tokens: int = None) -> dict:
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            return {"error": error_msg}

        request_id = str(uuid.uuid4())

        if self.api_style == "responses":
            return self._call_responses(messages, tools, max_tokens, request_id)

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "stream": False,
        }

        # chat/completions honors a top-level `reasoning` string for models that support it
        # (e.g. Gemma 4: "none" disables thinking — which also avoids its repetition loops).
        if self.reasoning is not None:
            payload["reasoning"] = self.reasoning

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if response_format and getattr(self, "_response_format_supported", True):
            payload["response_format"] = response_format

        session = self._get_session()

        _log_request_to_file(
            payload=payload,
            endpoint=self.api_endpoint,
            request_id=request_id,
            metadata={"method": "generate_with_tools", "model": self.model_name, "has_tools": bool(tools)}
        )

        t0 = time.perf_counter()
        try:
            response = session.post(
                self.api_endpoint,
                json=payload,
                headers=self._prepare_headers(),
                timeout=300
            )

            if response.status_code == 200:
                result = response.json()
                result = self._unwrap_response(result)
                _log_call(self.model_name, len(messages), bool(tools), time.perf_counter() - t0, result)
                self._log_llm(messages, result)
                _log_response_to_file(result, self.api_endpoint, request_id, {"method": "generate_with_tools", "model": self.model_name})
                return result
            elif response_format and "response_format" in response.text:
                logger.warning("Endpoint does not support response_format, retrying without it")
                self._response_format_supported = False
                payload.pop("response_format", None)
                response = session.post(self.api_endpoint, json=payload, headers=self._prepare_headers(), timeout=300)
                if response.status_code == 200:
                    result = response.json()
                    result = self._unwrap_response(result)
                    _log_call(self.model_name, len(messages), bool(tools), time.perf_counter() - t0, result)
                    self._log_llm(messages, result)
                    _log_response_to_file(result, self.api_endpoint, request_id, {"method": "generate_with_tools", "model": self.model_name})
                    return result
                error_msg = f"Status {response.status_code}: {response.text}"
                self._log_llm(messages, None, error_msg)
                return {"error": error_msg}
            else:
                error_msg = f"Status {response.status_code}: {response.text}"
                self._log_llm(messages, None, error_msg)
                _log_response_to_file({"error": error_msg}, self.api_endpoint, request_id, {"method": "generate_with_tools", "model": self.model_name})
                return {"error": error_msg}

        except requests.exceptions.RequestException as e:
            error_msg = f"Connection error: {str(e)}"
            self._log_llm(messages, None, error_msg)
            return {"error": error_msg}

    def _call_responses(self, messages: list, tools: list, max_tokens: Optional[int],
                        request_id: str) -> dict:
        """Call the OpenAI-compatible Responses endpoint, translating to/from chat shape.

        The Responses API is the only LM Studio path that honors reasoning.effort, so this is
        how we keep a local reasoning model from spending ~30k tokens thinking per node. Speaks
        the same {choices, usage} shape back to callers as generate_with_tools."""
        instructions, input_items = _chat_messages_to_responses_input(messages)
        payload = {
            "model": self.model_name,
            "input": input_items,
            "temperature": 0.7,
            "max_output_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "stream": False,
        }
        if instructions:
            payload["instructions"] = instructions
        if self.reasoning in _REASONING_EFFORTS:
            payload["reasoning"] = {"effort": self.reasoning}
        if tools:
            payload["tools"] = _chat_tools_to_responses(tools)
            payload["tool_choice"] = "auto"

        endpoint = f"{self._api_root}/responses"
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
        if self.api_style == "responses":
            # No SSE translation for the Responses API yet — signal callers to use the
            # non-streaming path (generate_with_tools), which handles responses. Both stream
            # consumers fall back to generate_with_tools on an error chunk.
            yield {"error": "streaming not supported for responses api_style"}
            return
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            yield {"error": error_msg}
            return

        request_id = str(uuid.uuid4())
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "stream": True,
        }

        if self.reasoning is not None:
            payload["reasoning"] = self.reasoning

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        session = self._get_session()

        _log_request_to_file(
            payload=payload,
            endpoint=self.api_endpoint,
            request_id=request_id,
            metadata={"method": "generate_with_tools_stream", "model": self.model_name, "has_tools": bool(tools), "streaming": True}
        )

        try:
            response = session.post(
                self.api_endpoint,
                json=payload,
                headers=self._prepare_headers(),
                stream=True,
                timeout=(10, 300)
            )

            if response.status_code == 200:
                chunks = []
                for line in response.iter_lines():
                    if not line:
                        continue

                    line = line.decode('utf-8')

                    if not line.strip() or line.startswith(':'):
                        continue

                    if line.startswith('data: '):
                        data_str = line[6:]

                        if data_str.strip() == '[DONE]':
                            break

                        try:
                            chunk = json.loads(data_str)
                            chunk = self._unwrap_response(chunk)
                            chunks.append(chunk)
                            yield chunk

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse streaming chunk: {e}")
                            continue
            else:
                error_msg = f"Status {response.status_code}: {response.text}"
                logger.error(f"API streaming error: {error_msg}")
                self._log_llm(messages, None, error_msg)
                _log_response_to_file({"error": error_msg}, self.api_endpoint, request_id, {"method": "generate_with_tools_stream", "model": self.model_name})
                yield {"error": error_msg}

        except requests.exceptions.RequestException as e:
            error_msg = f"Streaming connection error: {str(e)}"
            logger.error(f"API streaming error: {error_msg}")
            self._log_llm(messages, None, error_msg)
            yield {"error": error_msg}