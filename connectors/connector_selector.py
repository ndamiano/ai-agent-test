"""Connector selector for choosing appropriate AI connectors"""

from connectors.lmstudio_client import LMStudioConnector
from typing import Optional

class ConnectorSelector:
    def __init__(self):
        # Remove shared mutable state - each selector instance gets its own connector
        self._lmstudio_connector: Optional[LMStudioConnector] = None
    
    def getConnector(self, _caller: str, _task_type: str, _generation_type: str):
        """
        Get appropriate connector for the task
        
        Args:
            _caller: Which component is requesting the connector (unused for now)
            _task_type: Type of task (e.g. "character_creation", "world_building") (unused for now)
            _generation_type: Type of generation (e.g. "text", "image") (unused for now)
            
        Returns:
            AI connector instance
        """
        # For now, always return LMStudio connector
        # TODO: Add logic to choose connector based on parameters
        
        if self._lmstudio_connector is None:
            self._lmstudio_connector = LMStudioConnector()
        
        return self._lmstudio_connector

# Remove global singleton instance to avoid shared mutable state
# Each component should create its own ConnectorSelector instance
# _global_selector = ConnectorSelector()

def get_connector(caller: str = "unknown", task_type: str = "general", generation_type: str = "text"):
    """
    Convenience function to get a connector
    
    Usage:
        from connectors.connector_selector import get_connector
        ai = get_connector("world_generator", "world_building", "text")
    
    Note: This creates a new connector instance each time to avoid shared state.
    For better performance, components should create and reuse their own ConnectorSelector instance.
    """
    selector = ConnectorSelector()
    return selector.getConnector(caller, task_type, generation_type)
