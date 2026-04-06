"""Factory for creating OpenAI-compatible connectors with singleton caching"""

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


def reset_connector_cache() -> None:
    """Force invalidation of the cached connector. Call after settings changes."""
    global _cached_connector, _cached_settings_hash
    _cached_connector = None
    _cached_settings_hash = None


def get_connector(connector_type: Optional[str] = None, settings: Optional[Dict[str, Any]] = None, *args, **kwargs) -> OpenAICompatibleConnector:
    """
    Get a singleton OpenAI-compatible connector instance.

    Returns a cached connector if settings haven't changed, otherwise creates
    a new one. This ensures the health check and agents always use the same
    connector instance.

    Args:
        connector_type: The type of connector to use ('lmstudio' or 'cline'). 
                       If None, reads from global settings.
        settings: Optional settings dictionary to use instead of loading from manager.
                 Useful for testing or overriding configuration.
        *args, **kwargs: Ignored. Kept for backward compatibility with old ConnectorSelector usage.

    Returns:
        An instance of OpenAICompatibleConnector configured for the requested provider.
    """
    global _cached_connector, _cached_settings_hash

    global_settings = settings_manager.get_settings()
    
    # Handle old call signature: get_connector("type", "caller", "task_type", ...)
    # where extra args were strings, not a settings dict
    if not isinstance(settings, dict):
        settings = None
    
    # If connector_type is not a recognized provider, use global setting
    recognized_types = ("lmstudio", "cline", "openrouter")
    if connector_type not in recognized_types:
        connector_type = global_settings.get("connector_type", "lmstudio")
        
    if settings is None:
        settings = settings_manager.get_connector_settings(connector_type)

    # Check if cached connector is still valid
    settings_hash = _hash_settings(settings)
    if _cached_connector is not None and _cached_settings_hash == settings_hash:
        return _cached_connector

    # Extract configuration
    # api_key is optional (LMStudio doesn't have it, Cline does)
    api_key = settings.get("api_key")
    
    base_url = settings.get("base_url")
    if not base_url:
        # Fallback for legacy configs or missing fields
        if connector_type == "cline":
            base_url = "https://api.cline.bot/api"
        else:
            base_url = "http://localhost:1234"

    # Create new connector and update cache
    _cached_connector = OpenAICompatibleConnector(
        base_url=base_url,
        api_key=api_key,
        model=settings.get("model", "default"),
        temperature=settings.get("temperature", 0.7),
        max_tokens=settings.get("max_tokens", 50000)
    )
    _cached_settings_hash = settings_hash

    return _cached_connector
