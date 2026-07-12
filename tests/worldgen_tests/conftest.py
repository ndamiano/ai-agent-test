import copy
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_PIRATE_RECIPE = {
    "archetype": "archipelago",
    "size": "medium",
    "palette": {"biomes": ["tropical shallows", "reef", "beach", "jungle", "rocky highlands"]},
    "locations": [
        {"id": "home_port", "type": "settlement", "want": "coastal harbor"},
        {"id": "tavern", "type": "interior", "host": "home_port"},
        {"id": "maroon_beach", "type": "coastal_strip", "want": "remote from home_port"},
        {"id": "wilds", "type": "wilderness", "want": "jungle inland"},
        {"id": "sea", "type": "open_water", "want": "large"},
    ],
}


@pytest.fixture
def pirate_recipe():
    return copy.deepcopy(_PIRATE_RECIPE)
