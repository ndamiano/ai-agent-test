import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from config.settings_schema import DEFAULT_MODEL_CATEGORIES, ModelCategorySettings

logger = logging.getLogger(__name__)


def _merge(defaults: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """`loaded` over `defaults`, one level deep — the settings blocks (lmstudio, comfyui, …) are
    flat dicts, so a file that names a block need not repeat every key in it."""
    out = dict(defaults)
    for k, v in loaded.items():
        base = out.get(k)
        out[k] = {**base, **v} if isinstance(base, dict) and isinstance(v, dict) else v
    return out


class SettingsManager:

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

        # Control-plane state (the sqlite dbs), resolved against the repo root — deliberately
        # apart from working_directory, which holds generated artifacts.
        default_data_dir = os.getenv("MAESTRO_DATA_DIR", "data")
        if not Path(default_data_dir).is_absolute():
            default_data_dir = str((project_root.parent / default_data_dir).resolve())

        self.defaults = {
            "connector_type": os.getenv("CONNECTOR_TYPE", "lmstudio"),
            "working_directory": default_working_dir,
            "data_dir": default_data_dir,
            "model_category": os.getenv("MODEL_CATEGORY", "large"),
            "lmstudio": {
                "base_url": os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234"),
                "model": os.getenv("LMSTUDIO_MODEL", "local-model"),
                # "none" is the floor the fix loop escalates FROM. Absent this key an env-configured
                # deployment sends no reasoning field, and a thinking model spends a small token
                # budget entirely on reasoning.
                "reasoning": os.getenv("LMSTUDIO_REASONING", "none"),
                "max_tokens": 50000,
                "n_ctx": int(os.getenv("LMSTUDIO_N_CTX", "32768"))
            },
            "cline": {
                "api_key": os.getenv("CLINE_API_KEY", ""),
                "base_url": os.getenv("CLINE_BASE_URL", "https://api.cline.bot/api"),
                "model": os.getenv("CLINE_MODEL", "claude-sonnet-4-5"),
                "max_tokens": 50000
            },
            "comfyui": {
                "endpoint": os.getenv("COMFYUI_ENDPOINT", "http://localhost:8188")
            },
            "trellis": {
                "endpoint": os.getenv("TRELLIS_ENDPOINT", "http://localhost:8189")
            },
            "workqueue": {
                "enabled": os.getenv("WORKQUEUE_ENABLED", "false").lower() == "true",
                "token": os.getenv("WORKQUEUE_TOKEN", ""),
                "job_timeout_seconds": int(os.getenv("WORKQUEUE_JOB_TIMEOUT", "900")),
                "lease_seconds": int(os.getenv("WORKQUEUE_LEASE", "120"))
            },
            # _merge is one level deep, so a settings.json "runpod" block merges over these keys —
            # EXCEPT "queues", which it replaces wholesale: the file must carry complete queue blocks.
            "runpod": {
                "enabled": os.getenv("RUNPOD_ENABLED", "false").lower() == "true",
                "api_key": os.getenv("RUNPOD_API_KEY", ""),
                "network_volume_id": os.getenv("RUNPOD_NETWORK_VOLUME_ID", ""),
                "cloud_type": "SECURE",
                "cp_url": os.getenv("RUNPOD_CP_URL", ""),
                "tick_seconds": 15,
                "stale_worker_seconds": 180,
                "queues": {}
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
                    # Callers subscript this dict directly, so a key the file omits is a KeyError
                    # rather than the schema default it looks like — layer the defaults under it.
                    return _merge(self.defaults, settings)
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
        with self._settings_lock:
            return self._settings.copy()

    def get_connector_settings(self, connector_type: str) -> Dict[str, Any]:
        with self._settings_lock:
            return self._settings.get(connector_type, {}).copy()

    def get_category_settings(self):
        with self._settings_lock:
            category = self._settings.get("model_category", "large")
        return DEFAULT_MODEL_CATEGORIES.get(category, ModelCategorySettings())


# Global singleton instance
settings_manager = SettingsManager()
