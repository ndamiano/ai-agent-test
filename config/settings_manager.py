"""Settings manager for persistent configuration with JSON storage

Settings are stored in config/settings.json which is gitignored.
See config/settings.example.json for the default structure.
The settings file is created automatically on first run.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any
from threading import Lock
import logging

from pydantic import ValidationError as PydanticValidationError
from config.settings_schema import AppSettings

logger = logging.getLogger(__name__)


class SettingsManager:
    """Settings manager with JSON persistence"""

    def __init__(self):
        self._settings_lock = Lock()

        # Get project root directory
        project_root = Path(__file__).parent.parent
        self.settings_path = project_root / "config" / "settings.json"

        # Default settings
        # Get absolute path for working directory
        default_working_dir = os.getenv("WORKING_DIRECTORY", "outputs")
        if not Path(default_working_dir).is_absolute():
            default_working_dir = str((project_root / default_working_dir).resolve())

        self.defaults = {
            "connector_type": os.getenv("CONNECTOR_TYPE", "lmstudio"),
            "working_directory": default_working_dir,
            "lmstudio": {
                "base_url": os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234"),
                "model": os.getenv("LMSTUDIO_MODEL", "local-model"),
                "temperature": 0.7,
                "max_tokens": 50000
            },
            "cline": {
                "api_key": os.getenv("CLINE_API_KEY", ""),
                "base_url": os.getenv("CLINE_BASE_URL", "https://api.cline.bot/api"),
                "model": os.getenv("CLINE_MODEL", "claude-sonnet-4-5"),
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
        Validate settings structure and values using Pydantic schema.

        Args:
            settings: Settings dictionary to validate

        Raises:
            ValueError: If validation fails
        """
        try:
            AppSettings(**settings)
        except PydanticValidationError as e:
            raise ValueError(str(e)) from e

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
