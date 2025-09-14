from typing import Optional
from data_classes.world import WorldContext
from generators.world_generator import WorldGenerator
from repository.context_repository import ContextRepository

class Project:
    """
    A bounded creative project with its own context and generators.
    Examples: "Skyrim Mod", "D&D Campaign", "Visual Novel"
    """
    
    def __init__(self, name: str):
        """
        Initialize a new AI project
        
        Args:
            name: Human-readable project name (e.g. "Nordic Power Fantasy Mod")
        """
        self.name = name
        self.context_repo = ContextRepository()
        
        self.world_gen = WorldGenerator(self.context_repo)
    
    def __repr__(self):
        return f"Project('{self.name}')"