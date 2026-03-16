"""Connector selector for choosing appropriate AI connectors"""

from llm_clients.lmstudio_client import LMStudioConnector
from llm_clients.cline_client import ClineConnector
from typing import Optional, Union

class ConnectorSelector:
    def __init__(self, connector_type: Optional[str] = None):
        """
        Initialize connector selector

        Args:
            connector_type: Override connector type ('lmstudio' or 'cline').
                          If None, loads from settings.
        """
        self._connector_type = connector_type
        self._connector: Optional[Union[LMStudioConnector, ClineConnector]] = None

    def getConnector(self, _caller: str = "unknown", _task_type: str = "general", _generation_type: str = "text"):
        """
        Get appropriate connector for the task

        Args:
            _caller: Which component is requesting the connector (unused for now)
            _task_type: Type of task (e.g. "character_creation", "world_building") (unused for now)
            _generation_type: Type of generation (e.g. "text", "image") (unused for now)

        Returns:
            AI connector instance
        """
        if self._connector is None:
            # Determine connector type
            if self._connector_type is None:
                from config.settings_manager import settings_manager
                settings = settings_manager.get_settings()
                connector_type = settings.get("connector_type", "lmstudio")
            else:
                connector_type = self._connector_type

            # Create appropriate connector
            if connector_type == "cline":
                self._connector = ClineConnector()
            else:
                self._connector = LMStudioConnector()

        return self._connector

def get_connector(caller: str = "unknown", task_type: str = "general", generation_type: str = "text", connector_type: Optional[str] = None):
    """
    Convenience function to get a connector

    Usage:
        from llm_clients.connector_selector import get_connector
        ai = get_connector("world_generator", "world_building", "text")

        # Or with explicit connector type:
        ai = get_connector(connector_type="cline")

    Args:
        caller: Component requesting the connector
        task_type: Type of task
        generation_type: Type of generation
        connector_type: Override connector type ('lmstudio' or 'cline')

    Note: This creates a new connector instance each time to avoid shared state.
    For better performance, components should create and reuse their own ConnectorSelector instance.
    """
    # Create a new selector instance each time to avoid shared mutable state
    selector = ConnectorSelector(connector_type=connector_type)
    return selector.getConnector(caller, task_type, generation_type)
