from typing import Optional, Dict, Any
import hashlib
import json
from llm_clients.openai_compatible_connector import OpenAICompatibleConnector
from config.settings_manager import settings_manager

# Singleton cache
_cached_connector: Optional[OpenAICompatibleConnector] = None
_cached_settings_hash: Optional[str] = None


def _hash_settings(settings: Dict[str, Any]) -> str:
    """Create a stable hash of settings for cache invalidation."""
    return hashlib.md5(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def get_connector(connector_type: Optional[str] = None, settings: Optional[Dict[str, Any]] = None) -> OpenAICompatibleConnector:
    global _cached_connector, _cached_settings_hash

    global_settings = settings_manager.get_settings()

    if connector_type not in ("lmstudio", "cline", "openrouter"):
        connector_type = global_settings.get("connector_type", "lmstudio")

    if settings is None:
        settings = settings_manager.get_connector_settings(connector_type)

    settings_hash = _hash_settings(settings)
    if _cached_connector is not None and _cached_settings_hash == settings_hash:
        return _cached_connector

    api_key = settings.get("api_key")
    base_url = settings.get("base_url")
    if not base_url:
        base_url = "https://api.cline.bot/api" if connector_type == "cline" else "http://localhost:1234"

    _cached_connector = OpenAICompatibleConnector(
        base_url=base_url,
        api_key=api_key,
        model=settings.get("model", "default"),
        max_tokens=settings.get("max_tokens", 50000),
        frequency_penalty=settings.get("frequency_penalty", 0.5),
        reasoning=settings.get("reasoning"),
    )
    _cached_settings_hash = settings_hash

    return _cached_connector
