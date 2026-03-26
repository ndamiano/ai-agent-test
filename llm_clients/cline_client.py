"""Cline API connector"""

import logging
import requests
import json
import os
from typing import Any, Union, List, Dict, Optional
from llm_clients.base_connector import BaseConnector

logger = logging.getLogger(__name__)


class ClineConnector(BaseConnector):
    connector_name = "cline"

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        """
        Initialize Cline connector

        Args:
            settings: Optional settings dictionary. If not provided, will load from SettingsManager.
                     Expected keys: api_key, model, temperature (optional), max_tokens (optional)
        """
        super().__init__()
        # Load settings
        if settings is None:
            from config.settings_manager import settings_manager
            settings = settings_manager.get_connector_settings("cline")

        # Use environment variables as fallback
        self.api_key = settings.get("api_key", os.getenv("CLINE_API_KEY", ""))
        self.model_name = settings.get("model", os.getenv("CLINE_MODEL", "claude-sonnet-4-5"))
        self.temperature = settings.get("temperature", 0.7)
        self.max_tokens = settings.get("max_tokens", 50000)

        self.base_url = "https://api.cline.bot"
        self.api_endpoint = f"{self.base_url}/api/v1/chat/completions"
        self._connected = False
        self._last_health_check = 0
        self._health_check_interval = 30  # seconds

    def connect(self) -> bool:
        """
        Test connection to Cline API

        Returns:
            True if connection successful, False otherwise
        """
        if not self.api_key:
            logger.warning("Cline API key not configured")
            return False

        logger.debug(f"Testing connection to Cline API at {self.api_endpoint}")

        try:
            response = requests.post(
                self.api_endpoint,
                json={
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 1
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                },
                timeout=10
            )
            if response.status_code in [200, 400]:  # 400 is ok for health check
                self._connected = True
                logger.info(f"Successfully connected to Cline API (status {response.status_code})")
                return True
            else:
                logger.warning(f"Could not connect to Cline API: {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"Cline API not accessible: {e}")
            return False

    def health_check(self) -> bool:
        """
        Check if the connection is still healthy

        Returns:
            True if connection is healthy, False otherwise
        """
        if not self.api_key:
            return False

        if not self._connected:
            return self.connect()

        # For API services, we'll just check if we have an API key
        return bool(self.api_key)

    async def health_check_async(self) -> bool:
        import asyncio
        return await asyncio.to_thread(self.health_check)

    def generate(self, prompt: str, context: str) -> str:
        """
        Generate response using context and prompt

        Args:
            context: Context information to prepend
            prompt: User prompt/question

        Returns:
            Generated response from Cline
        """
        # Combine context and prompt
        if context.strip():
            full_prompt = f"Context:\n{context}\n\nUser Request:\n{prompt}"
        else:
            full_prompt = prompt

        # Prepare API request
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "user", "content": full_prompt}
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }

        logger.debug(f"Cline API request to {self.api_endpoint} with model {self.model_name}")

        # Use connection pooling for better performance
        session = self._get_session()

        try:
            response = session.post(
                self.api_endpoint,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                },
                timeout=60
            )

            logger.debug(f"Cline API response status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()

                # Cline API wraps the response in a "data" object
                if "data" in result:
                    data = result["data"]
                    logger.debug(f"Cline API returned success with {len(data.get('choices', []))} choices")
                    return data["choices"][0]["message"]["content"]
                else:
                    # Fallback for standard OpenAI format
                    response_content = result["choices"][0]["message"]["content"]
                    self._log_llm(full_prompt, response_content)
                    return response_content
            else:
                error_msg = f"Error: Cline API returned status {response.status_code}: {response.text}"
                logger.error(error_msg)
                self._log_llm(full_prompt, None, error_msg)
                return error_msg

        except requests.exceptions.RequestException as e:
            error_msg = f"Error connecting to Cline API: {str(e)}"
            logger.error(error_msg)
            self._log_llm(full_prompt, None, error_msg)
            raise RuntimeError(error_msg)
        except (KeyError, json.JSONDecodeError) as e:
            error_msg = f"Error parsing Cline API response: {str(e)}"
            logger.error(error_msg)
            logger.debug(f"Response content: {response.text[:500]}")
            self._log_llm(full_prompt, None, error_msg)
            raise RuntimeError(error_msg)

    def generate_with_tools(self, messages: list, tools: list = None) -> dict:
        """Generate response with optional tool support (non-streaming)"""

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False
        }

        # Add tools if provided
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"  # Let model decide when to use tools
            logger.debug(f"Cline API request with {len(tools)} tools available")

        logger.debug(f"Cline API request to {self.api_endpoint} with model {self.model_name}")

        session = self._get_session()

        try:
            response = session.post(
                self.api_endpoint,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                },
                timeout=300  # Increased timeout to 5 minutes
            )

            logger.debug(f"Cline API response status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()

                # Cline API wraps the response in a "data" object
                if "data" in result:
                    logger.debug(f"Cline API returned wrapped response (success={result.get('success')})")
                    self._log_llm(messages, result["data"])
                    return result["data"]
                else:
                    # Fallback for standard OpenAI format
                    logger.debug("Cline API returned standard OpenAI format")
                    self._log_llm(messages, result)
                    return result
            else:
                error_msg = f"Status {response.status_code}: {response.text}"
                logger.error(f"Cline API error: {error_msg}")
                self._log_llm(messages, None, error_msg)
                return {"error": error_msg}

        except requests.exceptions.RequestException as e:
            error_msg = f"Connection error: {str(e)}"
            logger.error(f"Cline API connection error: {error_msg}")
            self._log_llm(messages, None, error_msg)
            return {"error": error_msg}

    def generate_with_tools_stream(self, messages: list, tools: list = None):
        """
        Generate response with optional tool support (streaming).

        Yields chunks of the response as they arrive to prevent timeouts.

        Args:
            messages: List of message dicts
            tools: Optional list of tool definitions

        Yields:
            dict: Streaming response chunks
        """
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True
        }

        # Add tools if provided
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            logger.debug(f"Cline API streaming request with {len(tools)} tools available")

        logger.debug(f"Cline API streaming request to {self.api_endpoint} with model {self.model_name}")

        # Use connection pooling for better performance
        session = self._get_session()

        try:
            response = session.post(
                self.api_endpoint,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                },
                stream=True,
                timeout=(10, 300)  # (connection timeout, read timeout)
            )

            logger.debug(f"Cline API streaming response status: {response.status_code}")

            if response.status_code == 200:
                # Process the streaming response
                for line in response.iter_lines():
                    if not line:
                        continue

                    line = line.decode('utf-8')

                    # Skip empty lines and comments
                    if not line.strip() or line.startswith(':'):
                        continue

                    # Parse SSE format: "data: {json}"
                    if line.startswith('data: '):
                        data_str = line[6:]  # Remove "data: " prefix

                        # Check for end of stream
                        if data_str.strip() == '[DONE]':
                            break

                        try:
                            chunk = json.loads(data_str)

                            # Unwrap Cline API response if needed
                            if "data" in chunk:
                                yield chunk["data"]
                            else:
                                yield chunk

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse streaming chunk: {e}")
                            continue
            else:
                error_msg = f"Status {response.status_code}: {response.text}"
                logger.error(f"Cline API streaming error: {error_msg}")
                self._log_llm(messages, None, error_msg)
                yield {"error": error_msg}

        except requests.exceptions.RequestException as e:
            error_msg = f"Streaming connection error: {str(e)}"
            logger.error(f"Cline API streaming error: {error_msg}")
            self._log_llm(messages, None, error_msg)
            yield {"error": error_msg}
