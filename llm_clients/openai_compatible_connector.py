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
    """Simple and dirty logging of AI model requests to a file"""
    # Essentially this is a debug variable, but lazy
    if false:
        return

    log_file = "logs/llm_requests.log"

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "endpoint": endpoint,
        "payload": payload,
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
        """
        Initialize OpenAI-compatible connector

        Args:
            base_url: The base URL of the API (e.g., http://localhost:1234 or https://api.openai.com/v1).
                     Should NOT include /chat/completions.
            api_key: Optional API key for authentication.
            model: The model name to use.
            temperature: The temperature setting for generation.
            max_tokens: The maximum number of tokens to generate.
        """
        super().__init__()
        
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Construct the endpoint. 
        # Standard OpenAI structure is {base_url}/v1/chat/completions.
        # If base_url already includes /v1 (or /api/v1), we assume the user provided the correct prefix.
        # However, to be safe and standard:
        # If base_url ends in /v1 or /v1/ or similar, we might double up.
        # Let's assume the user provides the root (e.g. http://localhost:1234) OR the full prefix (e.g. http://localhost:1234/v1).
        # To support both LMStudio (root) and our adjusted Cline config (root/api), we'll check if we should append /v1.
        # Actually, the prompt implies 'base_url' is the base. 
        # LMStudio uses http://localhost:1234 -> /v1/chat/completions
        # Cline (adjusted) uses https://api.cline.bot/api -> /v1/chat/completions
        # So we always append /v1/chat/completions?
        # Wait, OpenAI's base_url is usually https://api.openai.com/v1.
        # If I use that, and append /v1/chat/completions, it becomes .../v1/v1/chat/completions.
        # So we should NOT blindly append /v1.
        # Let's check if 'v1' (or 'v2', etc) is already in the path.
        
        if self._is_versioned_path(self.base_url):
             self.api_endpoint = f"{self.base_url}/chat/completions"
        else:
             self.api_endpoint = f"{self.base_url}/v1/chat/completions"

        self._connected = False
        self._last_health_check = 0
        self._health_check_interval = 30  # seconds

    def _is_versioned_path(self, url: str) -> bool:
        """Check if the URL path seems to already include a version prefix (e.g. /v1, /api/v1)."""
        # Simple check: does the path end with /v1, /v2, /api/v1, etc?
        # Or more simply, does it contain 'v1' or 'v2' in the last segment?
        path = url.split('?')[0]  # remove query params
        parts = path.strip('/').split('/')
        last_part = parts[-1] if parts else ""
        # Check if last part looks like a version (v1, v2, api-v1)
        return last_part.startswith('v') and last_part[1:].isdigit()

    def connect(self) -> bool:
        """
        Test connection to the API

        Returns:
            True if connection successful, False otherwise
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Try to fetch models list as a health check
        # Construct models URL based on endpoint
        models_url = self.api_endpoint.replace("/chat/completions", "/models")

        try:
            response = requests.get(models_url, headers=headers, timeout=5)
            if response.status_code == 200:
                self._connected = True
                return True
            
            # Some APIs might not support /models but are still valid (e.g. some proxies)
            # If we get a 401, we definitely have a connection but bad auth.
            if response.status_code == 401:
                self._connected = True
                logger.warning("Connected but unauthorized (401). Check API key.")
                return True
                
            logger.warning(f"Could not connect to API at {models_url} (status {response.status_code})")
            return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"API not accessible at {models_url}: {e}")
            return False

    def health_check(self) -> bool:
        """
        Check if the connection is still healthy

        Returns:
            True if connection is healthy, False otherwise
        """
        if not self._connected:
            return self.connect()
        
        # For API services, if we have an api_key we assume it's healthy unless proven otherwise
        # If it's local (no key), we should check periodically
        if not self.api_key:
            return self.connect()
            
        return True

    async def health_check_async(self) -> bool:
        import httpx
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        models_url = self.api_endpoint.replace("/chat/completions", "/models")
        
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(models_url, headers=headers)
                return response.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            self._connected = False
            return False

    def _prepare_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _unwrap_response(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Unwrap response if it's nested in a 'data' object (common in some proxies like Cline)"""
        if "data" in result and "choices" not in result:
            return result["data"]
        return result

    def generate(self, prompt: str, context: str) -> str:
        """
        Generate response using context and prompt

        Args:
            context: Context information to prepend
            prompt: User prompt/question

        Returns:
            Generated response from API
        """
        # Apply rate limiting (wait up to 10 seconds)
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
        """Generate response with optional tool support"""

        # Apply rate limiting (wait up to 10 seconds)
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
        """
        Generate response with optional tool support (streaming).

        Yields chunks of the response as they arrive.

        Args:
            messages: List of message dicts
            tools: Optional list of tool definitions

        Yields:
            dict: Streaming response chunks
        """
        # Apply rate limiting (wait up to 10 seconds)
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