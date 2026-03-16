"""Settings manager for persistent configuration with JSON storage

Settings are stored in config/settings.json which is gitignored.
See config/settings.example.json for the default structure.
The settings file is created automatically on first run.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from threading import Lock
import logging

logger = logging.getLogger(__name__)


class SettingsManager:
    """Singleton settings manager with JSON persistence"""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Only initialize once
        if hasattr(self, '_initialized'):
            return

        self._initialized = True
        self._settings_lock = Lock()

        # Get project root directory
        project_root = Path(__file__).parent.parent
        self.settings_path = project_root / "config" / "settings.json"

        # Default settings
        self.defaults = {
            "connector_type": "lmstudio",
            "lmstudio": {
                "base_url": os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234"),
                "model": os.getenv("LMSTUDIO_MODEL", "local-model"),
                "temperature": 0.7,
                "max_tokens": 50000
            }
        }

        # Load or create settings
        self._settings = self._load_settings()

    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from JSON file, create with defaults if doesn't exist"""
        try:
            if self.settings_path.exists():
                with open(self.settings_path, 'r') as f:
                    settings = json.load(f)
                    logger.info(f"Settings loaded from {self.settings_path}")
                    return settings
            else:
                # Create settings file with defaults
                logger.info(f"Settings file not found, creating with defaults at {self.settings_path}")
                self._save_settings(self.defaults)
                return self.defaults.copy()
        except Exception as e:
            logger.error(f"Error loading settings: {e}, using defaults")
            return self.defaults.copy()

    def _save_settings(self, settings: Dict[str, Any]) -> None:
        """Save settings to JSON file"""
        try:
            # Ensure config directory exists
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.settings_path, 'w') as f:
                json.dump(settings, f, indent=2)

            logger.info(f"Settings saved to {self.settings_path}")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            raise

    def get_settings(self) -> Dict[str, Any]:
        """Get current settings (thread-safe)"""
        with self._settings_lock:
            return self._settings.copy()

    def update_settings(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update settings with validation (thread-safe)

        Args:
            new_settings: New settings dictionary

        Returns:
            Updated settings

        Raises:
            ValueError: If settings validation fails
        """
        # Validate settings
        self._validate_settings(new_settings)

        with self._settings_lock:
            self._settings = new_settings
            self._save_settings(new_settings)
            return self._settings.copy()

    def _validate_settings(self, settings: Dict[str, Any]) -> None:
        """
        Validate settings structure and values

        Args:
            settings: Settings dictionary to validate

        Raises:
            ValueError: If validation fails
        """
        # Check required keys
        if "connector_type" not in settings:
            raise ValueError("Missing required field: connector_type")

        # Validate connector type
        valid_connectors = ["lmstudio"]  # Can expand in future
        if settings["connector_type"] not in valid_connectors:
            raise ValueError(f"Invalid connector_type. Must be one of: {valid_connectors}")

        # Validate lmstudio settings
        if "lmstudio" not in settings:
            raise ValueError("Missing required field: lmstudio")

        lm = settings["lmstudio"]
        if not isinstance(lm, dict):
            raise ValueError("lmstudio must be a dictionary")

        if "base_url" not in lm or not lm["base_url"]:
            raise ValueError("lmstudio.base_url is required")

        if "model" not in lm or not lm["model"]:
            raise ValueError("lmstudio.model is required")

        # Validate optional numeric fields
        if "temperature" in lm:
            temp = lm["temperature"]
            if not isinstance(temp, (int, float)) or temp < 0 or temp > 2:
                raise ValueError("lmstudio.temperature must be between 0 and 2")

        if "max_tokens" in lm:
            tokens = lm["max_tokens"]
            if not isinstance(tokens, int) or tokens < 1:
                raise ValueError("lmstudio.max_tokens must be a positive integer")

    def get_connector_settings(self, connector_type: str) -> Dict[str, Any]:
        """
        Get settings for a specific connector

        Args:
            connector_type: Type of connector ('lmstudio', 'embedding', etc.)

        Returns:
            Connector-specific settings dictionary
        """
        with self._settings_lock:
            return self._settings.get(connector_type, {}).copy()


# Global singleton instance
settings_manager = SettingsManager()
