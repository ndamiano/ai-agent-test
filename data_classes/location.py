"""Location-related data classes"""

from dataclasses import dataclass
from typing import List, Optional

@dataclass
class LocationContext:
    name: str
    location_type: str
    tech_level: str
    population: int
    controlling_faction: Optional[str]
    local_culture: str
    notable_features: List[str]
    local_npcs: List[str]