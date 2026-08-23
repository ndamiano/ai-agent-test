"""bake_scene writes an interior's ground image and its logic file into the game folder,
synchronously — pure CPU, so a build turn can wait for it. Outdoor archetypes go through
codegen/scene_chain.py instead.

The model asks for a PLACE (archetype + style + seed) and gets back pixels it never has to reason
about plus a scene.json it must read — the walkable grid and the rooms.
"""

import json
import random
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from scenegen.ground import band_inside, cell_mask, flat_speckle
from scenegen.light import COLD, WARM, apply_lights

CELL = 48
MAX_CELLS = 64

# palette rows: grass, grass_speck, road, plaza, wall, trim, roof, door, window
_PALETTES = {
    "default": ((118, 178, 84), (106, 164, 74), (228, 214, 166), (202, 196, 182),
                (238, 226, 204), (122, 94, 70), (178, 62, 48), (96, 60, 28), (255, 232, 150)),
    "dark": ((84, 96, 78), (74, 86, 70), (150, 138, 120), (120, 116, 110),
             (92, 88, 100), (60, 56, 70), (56, 60, 76), (30, 26, 34), (120, 190, 220)),
    "sand": ((214, 190, 140), (202, 178, 130), (232, 214, 172), (222, 208, 178),
             (226, 208, 172), (150, 118, 80), (176, 96, 60), (110, 70, 36), (255, 238, 170)),
    "snow": ((226, 232, 240), (212, 220, 232), (198, 200, 208), (216, 218, 226),
             (206, 196, 186), (120, 100, 88), (140, 60, 52), (90, 56, 30), (255, 226, 140)),
    "wood": ((150, 132, 102), (140, 122, 94), (196, 178, 146), (214, 202, 174),
             (122, 96, 70), (84, 64, 46), (86, 92, 110), (70, 50, 34), (250, 226, 150)),
}
_KEYWORDS = {"dark": ("dark", "gothic", "horror", "night", "vampire", "cyber", "neon", "dungeon"),
             "sand": ("desert", "sand", "beach", "dune"),
             "snow": ("snow", "ice", "winter", "frozen"),
             "wood": ("japanese", "edo", "eastern", "bamboo")}


def _palette(style: str):
    s = (style or "").lower()
    for name, words in _KEYWORDS.items():
        if any(w in s for w in words):
            return _PALETTES[name]
    return _PALETTES["default"]


def _clamp(v: Optional[int], default: int) -> int:
    try:
        return max(12, min(MAX_CELLS, int(v)))
    except (TypeError, ValueError):
        return default


def bake_scene(root: Path, scene_id: str, archetype: str, style: str,
               seed: Optional[int] = None, width_cells: Optional[int] = None,
               height_cells: Optional[int] = None) -> dict:
    if not scene_id:
        raise KeyError("id")
    if archetype not in ("interior", "dungeon"):
        raise ValueError(f"bake_scene takes interior or dungeon, not {archetype!r}")
    seed = int(seed) if seed is not None else random.Random(scene_id).randint(0, 9999)
    w, h = _clamp(width_cells, 26), _clamp(height_cells, 18)
    pal = _palette(style)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    ground_rel = f"assets/{scene_id}_ground.png"
    json_rel = f"assets/{scene_id}_scene.json"

    img, data = _bake_rooms(w, h, seed, pal, dark=(archetype == "dungeon"))

    img.save(root / ground_rel)
    data.update({"archetype": archetype, "seed": seed, "cell_px": CELL,
                 "width_cells": w, "height_cells": h, "ground": ground_rel})
    (root / json_rel).write_text(json.dumps(data), encoding="utf-8")
    return {"ok": True, "files": [ground_rel, json_rel],
            "note": f"scene built. Read {json_rel} for the walkable grid and the rooms; "
                    f"draw {ground_rel} as the map background, one cell = "
                    f"{CELL}px."}


def _walkable_strings(walk: np.ndarray):
    return ["".join("1" if c else "0" for c in row) for row in walk]


def _bake_rooms(w, h, seed, pal, dark: bool):
    rnd = random.Random(seed)
    grass, speck, road_c, plaza_c, wall, trim, *_ = pal
    grid = np.zeros((h, w), bool)
    rooms = []
    for _ in range(rnd.randint(4, 7)):
        rw, rh = rnd.randint(4, 8), rnd.randint(3, 6)
        x, y = rnd.randint(1, max(2, w - rw - 2)), rnd.randint(1, max(2, h - rh - 2))
        rooms.append((x, y, rw, rh))
        grid[y:y + rh, x:x + rw] = True
    for (x1, y1, w1, h1), (x2, y2, w2, h2) in zip(rooms, rooms[1:]):
        cx1, cy1, cx2, cy2 = x1 + w1 // 2, y1 + h1 // 2, x2 + w2 // 2, y2 + h2 // 2
        grid[cy1, min(cx1, cx2):max(cx1, cx2) + 1] = True
        grid[min(cy1, cy2):max(cy1, cy2) + 1, cx2] = True
    floor_px = cell_mask(grid, CELL)
    ph, pw = h * CELL, w * CELL
    img_np = np.zeros((ph, pw, 3), np.uint8)
    img_np[:] = (14, 12, 18) if dark else (30, 27, 33)
    floor = flat_speckle(ph, pw, road_c if not dark else (74, 70, 82),
                         plaza_c if not dark else (64, 60, 72))
    img_np[floor_px] = floor[floor_px]
    img_np[band_inside(floor_px, 6)] = tuple(int(c * 0.6) for c in
                                             (road_c if not dark else (74, 70, 82)))
    pil = Image.fromarray(img_np)
    pools = []
    for x, y, rw, rh in rooms:
        pools.append(((x + rw / 2) * CELL, (y + rh / 2) * CELL, 140, WARM if not dark else COLD))
    pil = Image.fromarray(apply_lights(np.asarray(pil), pools,
                                       ambient_level=0.92 if not dark else 0.8))
    return pil, {"doors": [], "rooms": [list(r) for r in rooms],
                 "walkable": _walkable_strings(grid)}
