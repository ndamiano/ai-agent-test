"""Shared base connector with common utility methods"""

import logging
import json
import requests
from typing import Any, Union, List, Dict, Optional
from threading import Lock

logger = logging.getLogger(__name__)


class BaseConnector:
    """Base class for LLM connectors. Subclasses must set connector_name."""

    connector_name: str = "unknown"

    def __init__(self):
        self.model_name: str = ""
        self._session = None
        self._session_lock = Lock()

    def _get_session(self) -> requests.Session:
        """Get or create a connection-pooled session for better performance."""
        with self._session_lock:
            if self._session is None:
                self._session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=10,
                    max_retries=3
                )
                self._session.mount('http://', adapter)
                self._session.mount('https://', adapter)
            return self._session

    def _log_llm(self, prompt: Any, response: Any, error: Optional[str] = None):
        """Log LLM interaction with task context from execution context"""
        if error:
            logger.error(f"LLM call to {self.connector_name} ({self.model_name}) failed: {error}")
        else:
            logger.debug(f"LLM call to {self.connector_name} ({self.model_name}) completed")
