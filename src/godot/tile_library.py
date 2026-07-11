"""Curated tile library — the terrain look ships from hand-picked art, not per-run generation.

A theme string ("worn path", "tropical shallows", "glimmerpond") resolves by keyword to one of
~20 semantic tile CLASSES; each class is one seamless texture in src/assets/tiles/<class>.png.
`overlay_library_tiles` runs inside the godot compile after the run's generated images are
copied: mode "library" (default) overwrites each theme's tile_<slug>.png with its class art
(falling back to whatever the run generated when no class matches), mode "generated" keeps the
run's own tiles and only fills MISSING files from the library. Swapping the whole world to
generated terrain later = flip the setting; the classes and call sites don't change.
"""

import shutil
from pathlib import Path
from typing import Dict, Optional

LIBRARY_DIR = Path(__file__).resolve().parents[1] / "assets" / "tiles"

# Ordered: first match wins, specific before generic. Class names are the base vocabulary the
# curated pack is drawn in (dirt/sand/gravel/grass/stone_tile/stone_rough/dirt_path/wood_floor
# + water/growth/structure classes the generated worlds actually emit).
_CLASS_KEYWORDS = (
    ("water_deep", ("deep", "abyss", "ocean", "open sea")),
    ("water_shallow", ("reef", "shallow", "lagoon", "tide", "pond", "lake", "river", "water",
                       "sea", "glimmer")),
    ("sand", ("sand", "beach", "dune", "shore", "desert")),
    ("snow", ("snow", "ice", "glacier", "frost")),
    ("marsh", ("marsh", "swamp", "bog", "mire")),
    ("gravel", ("gravel", "scree", "shale", "rubble")),
    ("stone_rough", ("rock", "highland", "mountain", "cliff", "crag", "peak", "stone ridge",
                     "volcan", "lava", "ash")),
    ("wood_floor", ("wooden floor", "plank", "deck", "timber floor", "floorboard")),
    ("dense_growth", ("jungle", "forest", "wood", "grove", "thicket", "gloom", "bramble",
                      "hedge")),
    ("dirt_path", ("path", "trail", "dirt road", "track", "worn")),
    ("stone_tile", ("cobble", "plaza", "street", "pavement", "paved", "flagstone", "tile")),
    ("roof", ("rooftop", "roof")),
    ("wall", ("wall", "palisade", "rampart", "brick")),
    ("dirt", ("dirt", "mud", "earth", "barren", "wasteland")),
    ("grass", ("meadow", "grass", "plain", "field", "pasture", "sugarplum", "enchanted")),
)
_FALLBACK_CLASS = "grass"


def class_for(theme: str, role: str = "open") -> str:
    lname = theme.lower()
    for cls, keywords in _CLASS_KEYWORDS:
        if any(k in lname for k in keywords):
            return cls
    return "wall" if role == "blocked" else _FALLBACK_CLASS


def library_file(theme: str, role: str = "open") -> Optional[Path]:
    path = LIBRARY_DIR / f"{class_for(theme, role)}.png"
    return path if path.exists() else None


def overlay_library_tiles(ir: Dict, images_dir: Path, mode: str) -> int:
    from renpy.fns import tile_slug

    # The full class pack always ships as lib_<class>.png — the runtime's parametric blockout
    # buildings texture their walls/roofs/doors from it regardless of which themes the world used.
    for f in LIBRARY_DIR.glob("*.png"):
        shutil.copy2(f, images_dir / f"lib_{f.name}")
    replaced = 0
    seen = set()
    for place in ir.get("places", []) or []:
        legend = (place.get("tiles") or {}).get("legend") or {}
        for spec in legend.values():
            theme = str(spec.get("theme", ""))
            role = str(spec.get("role", "open"))
            if not theme or theme in seen:
                continue
            seen.add(theme)
            dest = images_dir / f"tile_{tile_slug(theme)}.png"
            src = library_file(theme, role)
            if src is None:
                continue
            if mode == "library" or not dest.exists():
                shutil.copy2(src, dest)
                replaced += 1
                # generated transition variants blend the OLD tile pixels — against library
                # art they'd render a seam of foreign texture; drop them (the presenter falls
                # back to the two base tiles)
                slug = tile_slug(theme)
                for trans in list(images_dir.glob(f"tile_{slug}__*.png")) + \
                        list(images_dir.glob(f"tile_*__{slug}_*.png")):
                    trans.unlink()
                    trans.with_suffix(".png.import").unlink(missing_ok=True)
    return replaced
