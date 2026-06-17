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
        "payload": payload["messages"],
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


class OpenAICompatibleConnector(BaseConnector):
    connector_name = "openai_compatible"

    def __init__(self, base_url: str, api_key: Optional[str] = None, model: str = "default",
                 max_tokens: int = 50000, frequency_penalty: float = 0.5):
        super().__init__()

        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model
        self.max_tokens = max_tokens
        self.frequency_penalty = frequency_penalty
        self._response_format_supported = True
        self._context_length: Optional[int] = None
        
        if self._is_versioned_path(self.base_url):
             self.api_endpoint = f"{self.base_url}/chat/completions"
        else:
             self.api_endpoint = f"{self.base_url}/v1/chat/completions"

    def get_context_length(self) -> Optional[int]:
        """Return the loaded model's context window size by querying /v1/models.

        Result is cached on the instance. Falls back to None if the endpoint
        is unreachable or doesn't return context_length.
        """
        if self._context_length is not None:
            return self._context_length
        try:
            response = self._get_session().get(
                f"{self.base_url}/v1/models",
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
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "stream": False,
        }

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

    def generate_with_tools_stream(self, messages: list, tools: list = None):
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