import copy
import sys
from pathlib import Path

import pytest

# tests/ is also on sys.path (see tests/conftest.py), and this directory's own
# name collides with the top-level `worldgen` package under src/ as a namespace
# package. Force src/ ahead of it so `import worldgen` resolves to the real package.
_SRC = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
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
