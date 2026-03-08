"""Generic project container for the agentic loop"""

from typing import Optional
from tools.tool_manager import ToolManager


class Project:
    """
    A generic project container with a tool manager.
    No worldbuilding-specific logic or dependencies.
    """
    
    def __init__(self, name: str):
        """
        Initialize a new project
        
        Args:
            name: Human-readable project name
        """
        self.name = name
        self.tool_manager = ToolManager()
    
    def __repr__(self):
        return f"Project('{self.name}')"