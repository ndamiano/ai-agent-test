import hashlib
import json
from typing import Any, Dict, Optional

from config.settings_manager import settings_manager
from llm_clients.openai_compatible_connector import OpenAICompatibleConnector
from llm_clients.queue_connector import QueueConnector

# Singleton cache
_cached_connector: Optional[OpenAICompatibleConnector] = None
_cached_settings_hash: Optional[str] = None


def _hash_settings(settings: Dict[str, Any]) -> str:
    """Create a stable hash of settings for cache invalidation."""
    return hashlib.md5(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def get_connector() -> OpenAICompatibleConnector:
    global _cached_connector, _cached_settings_hash

    global_settings = settings_manager.get_settings()
    settings = global_settings.get("llm") or {}

    # workqueue.enabled swaps the transport (jobs table + worker agents) while keeping the
    # provider settings (model, budgets, reasoning) — so it participates in cache invalidation.
    workqueue = global_settings.get("workqueue") or {}
    settings_hash = _hash_settings({**settings, "_workqueue": workqueue})
    if _cached_connector is not None and _cached_settings_hash == settings_hash:
        return _cached_connector

    kwargs = dict(
        base_url=settings.get("base_url", "http://localhost:1234"),
        model=settings.get("model", "default"),
        max_tokens=settings.get("max_tokens", 50000),
        frequency_penalty=settings.get("frequency_penalty", 0.5),
        reasoning=settings.get("reasoning"),
    )
    if workqueue.get("enabled"):
        _cached_connector = QueueConnector(
            **kwargs, queue="llm",
            job_timeout_seconds=workqueue.get("job_timeout_seconds", 900))
    else:
        _cached_connector = OpenAICompatibleConnector(**kwargs)
    _cached_settings_hash = settings_hash

    return _cached_connector
