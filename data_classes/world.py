"""World-related data classes"""

from dataclasses import dataclass
from typing import List, Optional

@dataclass  
class WorldContext:
    name: str
    races: List[str]
    major_locations: List[str]
    minor_locations: List[str]
    notable_npcs: List[str]
    major_world_events: List[str]
    factions: List[str]
    
    # World-wide systems (defaults/baselines)
    default_tech_level: str = "medieval"
    magic_system: Optional[str] = None
    political_system: str = "feudal"
    geography: Optional[str] = None
    religions: List[str] = None
    
    def __post_init__(self):
        """Initialize empty lists if None"""
        if self.religions is None:
            self.religions = []
    
    def to_context(self, focus: str = "general") -> str:
        """
        Convert world to context string for AI consumption
        
        Args:
            focus: What aspect to emphasize ("general", "character_creation", "world_building", "locations")
        """
        context_parts = [f"World: {self.name}"]
        
        if focus in ["character_creation", "general"]:
            if self.races:
                context_parts.append(f"Available Races: {', '.join(self.races)}")
            if self.factions:
                context_parts.append(f"Major Factions: {', '.join(self.factions)}")
            if self.notable_npcs:
                context_parts.append(f"Notable NPCs: {', '.join(self.notable_npcs)}")
        
        if focus in ["locations", "general"]:
            if self.major_locations:
                context_parts.append(f"Major Locations: {', '.join(self.major_locations)}")
            if self.minor_locations and len(self.minor_locations) <= 5:
                context_parts.append(f"Notable Areas: {', '.join(self.minor_locations)}")
        
        if focus in ["world_building", "general"]:
            context_parts.append(f"Political System: {self.political_system}")
            context_parts.append(f"Default Technology: {self.default_tech_level}")
            
            if self.magic_system:
                context_parts.append(f"Magic System: {self.magic_system}")
            if self.geography:
                context_parts.append(f"Geography: {self.geography}")
            if self.religions:
                context_parts.append(f"Major Religions: {', '.join(self.religions)}")
            if self.major_world_events:
                context_parts.append(f"Major Events: {', '.join(self.major_world_events)}")
        
        return "\n".join(context_parts)