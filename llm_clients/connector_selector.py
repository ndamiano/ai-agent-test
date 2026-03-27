"""Factory for creating OpenAI-compatible connectors"""

from typing import Optional, Dict, Any
from llm_clients.openai_compatible_connector import OpenAICompatibleConnector
from config.settings_manager import settings_manager

def get_connector(connector_type: Optional[str] = None, settings: Optional[Dict[str, Any]] = None, *args, **kwargs) -> OpenAICompatibleConnector:
    """
    Factory function to get an OpenAI-compatible connector instance.

    Args:
        connector_type: The type of connector to use ('lmstudio' or 'cline'). 
                       If None, reads from global settings.
        settings: Optional settings dictionary to use instead of loading from manager.
                 Useful for testing or overriding configuration.
        *args, **kwargs: Ignored. Kept for backward compatibility with old ConnectorSelector usage.

    Returns:
        An instance of OpenAICompatibleConnector configured for the requested provider.
    """
    global_settings = settings_manager.get_settings()
    
    # Handle old call signature: get_connector("type", "caller", "task_type", ...)
    # where extra args were strings, not a settings dict
    if not isinstance(settings, dict):
        settings = None
    
    # If connector_type is not a recognized provider, use global setting
    recognized_types = ("lmstudio", "cline")
    if connector_type not in recognized_types:
        connector_type = global_settings.get("connector_type", "lmstudio")
        
    if settings is None:
        settings = settings_manager.get_connector_settings(connector_type)
        
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

    return OpenAICompatibleConnector(
        base_url=base_url,
        api_key=api_key,
        model=settings.get("model", "default"),
        temperature=settings.get("temperature", 0.7),
        max_tokens=settings.get("max_tokens", 50000)
    )