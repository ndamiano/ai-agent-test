"""LMStudio local API connector"""

import logging
import requests
import json
import os
from typing import Any, Union, List, Dict, Optional
from threading import Lock

logger = logging.getLogger(__name__)


class LMStudioConnector:
    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        """
        Initialize LMStudio connector

        Args:
            settings: Optional settings dictionary. If not provided, will load from SettingsManager.
                     Expected keys: base_url, model, temperature (optional), max_tokens (optional)
        """
        # Load settings
        if settings is None:
            from config.settings_manager import settings_manager
            settings = settings_manager.get_connector_settings("lmstudio")

        # Use environment variables as fallback
        self.base_url = settings.get("base_url", os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234")).rstrip('/')
        self.model_name = settings.get("model", os.getenv("LMSTUDIO_MODEL", "local-model"))
        self.temperature = settings.get("temperature", 0.7)
        self.max_tokens = settings.get("max_tokens", 50000)

        self.api_endpoint = f"{self.base_url}/v1/chat/completions"
        self._connected = False
        self._session = None
        self._session_lock = Lock()
        self._last_health_check = 0
        self._health_check_interval = 30  # seconds

    def connect(self) -> bool:
        """
        Test connection to LMStudio server
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            if response.status_code == 200:
                self._connected = True
                return True
            else:
                logger.warning(f"Could not connect to LMStudio at {self.base_url}")
                return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"LMStudio not accessible at {self.base_url}: {e}")
            return False

    def health_check(self) -> bool:
        """
        Check if the connection is still healthy
        
        Returns:
            True if connection is healthy, False otherwise
        """
        if not self._connected:
            return self.connect()
        
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            self._connected = False
            return False

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
        
        # Handle single object vs list
        if not isinstance(context_data, list):
            context_data = [context_data]
        
        context_parts = []
        
        for item in context_data:
            if hasattr(item, '__dict__'):  # Dataclass or custom object
                # Convert object to dict and format nicely
                item_dict = item.__dict__ if hasattr(item, '__dict__') else vars(item)
                context_parts.append(self._format_dict(item_dict, item.__class__.__name__))
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
                # Configure session for better performance
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=10,
                    max_retries=3
                )
                self._session.mount('http://', adapter)
                self._session.mount('https://', adapter)
            return self._session
    
    def generate(self, prompt: str, context: str) -> str:
        """
        Generate response using context and prompt
        
        Args:
            context: Context information to prepend
            prompt: User prompt/question
            
        Returns:
            Generated response from LMStudio
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
        
        # Use connection pooling for better performance
        session = self._get_session()
        
        try:
            response = session.post(
                self.api_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                return f"Error: LMStudio returned status {response.status_code}: {response.text}"
                
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Error connecting to LMStudio: {str(e)}")
        except (KeyError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Error parsing LMStudio response: {str(e)}")

    def generate_with_tools(self, messages: list, tools: list = None) -> dict:
        """Generate response with optional tool support"""

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        
        # Add tools if provided
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"  # Let model decide when to use tools
        
        try:
            response = requests.post(
                self.api_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"Status {response.status_code}: {response.text}"}
                
        except requests.exceptions.RequestException as e:
            return {"error": f"Connection error: {str(e)}"}