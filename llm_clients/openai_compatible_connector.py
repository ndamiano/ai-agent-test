"""OpenAI-compatible API connector"""

import logging
import requests
import json
import os
from typing import Any, Union, List, Dict, Optional
from datetime import datetime
from llm_clients.base_connector import BaseConnector
from llm_clients.rate_limiter import get_llm_rate_limiter

logger = logging.getLogger(__name__)


def _log_request_to_file(payload: dict, endpoint: str, metadata: dict = None):
    log_file = "logs/llm_requests.log"

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "endpoint": endpoint,
        "payload": payload["messages"],
        "metadata": metadata or {}
    }

    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry, indent=2))
            f.write("\n" + "="*80 + "\n")
    except Exception as e:
        logger.warning(f"Failed to log request to file: {e}")


class OpenAICompatibleConnector(BaseConnector):
    connector_name = "openai_compatible"

    def __init__(self, base_url: str, api_key: Optional[str] = None, model: str = "default",
                 temperature: float = 0.7, max_tokens: int = 50000):
        super().__init__()
        
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        
        if self._is_versioned_path(self.base_url):
             self.api_endpoint = f"{self.base_url}/chat/completions"
        else:
             self.api_endpoint = f"{self.base_url}/v1/chat/completions"

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

    def generate(self, prompt: str, context: str) -> str:
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            raise RuntimeError(error_msg)

        if context.strip():
            full_prompt = f"Context:\n{context}\n\nUser Request:\n{prompt}"
        else:
            full_prompt = prompt

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "user", "content": full_prompt}
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }

        session = self._get_session()

        # Log the request
        _log_request_to_file(
            payload=payload,
            endpoint=self.api_endpoint,
            metadata={"method": "generate", "model": self.model_name}
        )

        try:
            response = session.post(
                self.api_endpoint,
                json=payload,
                headers=self._prepare_headers(),
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                result = self._unwrap_response(result)
                response_content = result["choices"][0]["message"]["content"]
                self._log_llm(full_prompt, response_content)
                return response_content
            else:
                error_msg = f"Error: API returned status {response.status_code}: {response.text}"
                self._log_llm(full_prompt, None, error_msg)
                return error_msg

        except requests.exceptions.RequestException as e:
            error_msg = f"Error connecting to API: {str(e)}"
            self._log_llm(full_prompt, None, error_msg)
            raise RuntimeError(error_msg)
        except (KeyError, json.JSONDecodeError) as e:
            error_msg = f"Error parsing API response: {str(e)}"
            self._log_llm(full_prompt, None, error_msg)
            raise RuntimeError(error_msg)

    def generate_with_tools(self, messages: list, tools: list = None) -> dict:
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            return {"error": error_msg}

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        session = self._get_session()

        # Log the request
        _log_request_to_file(
            payload=payload,
            endpoint=self.api_endpoint,
            metadata={"method": "generate_with_tools", "model": self.model_name, "has_tools": bool(tools)}
        )

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
                self._log_llm(messages, result)
                return result
            else:
                error_msg = f"Status {response.status_code}: {response.text}"
                self._log_llm(messages, None, error_msg)
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

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        session = self._get_session()

        # Log the request
        _log_request_to_file(
            payload=payload,
            endpoint=self.api_endpoint,
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
                            yield chunk

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse streaming chunk: {e}")
                            continue
            else:
                error_msg = f"Status {response.status_code}: {response.text}"
                logger.error(f"API streaming error: {error_msg}")
                self._log_llm(messages, None, error_msg)
                yield {"error": error_msg}

        except requests.exceptions.RequestException as e:
            error_msg = f"Streaming connection error: {str(e)}"
            logger.error(f"API streaming error: {error_msg}")
            self._log_llm(messages, None, error_msg)
            yield {"error": error_msg}