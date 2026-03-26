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

    def contextualize(self, context_data: Union[Any, List[Any]]) -> str:
        """
        Flatten generic objects into a readable string context

        Args:
            context_data: Object(s) to convert to string context

        Returns:
            Formatted string representation of the data
        """
        if context_data is None:
            return ""

        if not isinstance(context_data, list):
            context_data = [context_data]

        context_parts = []

        for item in context_data:
            if hasattr(item, '__dict__'):
                context_parts.append(self._format_dict(item.__dict__, item.__class__.__name__))
            elif isinstance(item, dict):
                context_parts.append(self._format_dict(item))
            elif isinstance(item, (list, tuple)):
                context_parts.append(f"List: {', '.join(str(x) for x in item)}")
            else:
                context_parts.append(str(item))

        return "\n\n".join(context_parts)

    def _format_dict(self, data: dict, type_name: str = "Data") -> str:
        """Helper to format dictionaries nicely"""
        lines = [f"{type_name}:"]
        for key, value in data.items():
            if isinstance(value, (list, tuple)) and value:
                if len(value) <= 3:
                    lines.append(f"  {key}: {', '.join(str(v) for v in value)}")
                else:
                    lines.append(f"  {key}: {', '.join(str(v) for v in value[:3])}... ({len(value)} total)")
            elif isinstance(value, dict):
                lines.append(f"  {key}: {json.dumps(value, indent=4)}")
            else:
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)

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
        try:
            from tools.execution_context import get_execution_context
            ctx = get_execution_context()
            task_id = ctx.get('task_id')
            subtask_id = ctx.get('subtask_id')
        except Exception:
            task_id = None
            subtask_id = None

        from tools.logging_utils import tool_logger
        tool_logger.log_llm_interaction(
            connector=self.connector_name,
            model=self.model_name,
            prompt=prompt,
            response=response,
            task_id=task_id,
            subtask_id=subtask_id,
            error=error
        )
