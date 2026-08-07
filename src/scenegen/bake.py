"""The platform face of scenegen: bake_scene writes a game-ready ground image and its logic file
into the game folder, synchronously — pure CPU, so a build turn can wait for it.

The split the tool contract encodes: the model asks for a PLACE (archetype + style + seed) and
gets back pixels it never has to reason about plus a scene.json it must read — door cells, the
walkable grid, POIs. Part sprites are code-drawn from a style-keyed palette for now; rendering
them through the image queue in the game's own style is the marked upgrade, and it changes only
the kit construction here.
"""

import json
import random
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

from scenegen.compose import compose_scene, paste_with_shadow, zone_masks
from scenegen.ground import band_inside, band_outside, cell_mask, flat_speckle
from scenegen.kit import BuildingKit
from scenegen.layouts import glade_layout, town_layout
from scenegen.light import COLD, WARM, apply_lights
from scenegen.scatter import Scatterer

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


def _drawn_part(w: int, h: int, body, edge) -> Image.Image:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], 4, fill=body + (255,), outline=edge + (255,),
                        width=2)
    d.line([(w // 2, 4), (w // 2, h - 4)], fill=edge + (255,), width=1)
    return im


def _kit(pal) -> BuildingKit:
    _, _, _, _, wall, trim, roof, door_c, win_c = pal
    door = _drawn_part(30, 42, door_c, tuple(int(c * 0.6) for c in door_c))
    win = _drawn_part(28, 26, win_c, trim)
    return BuildingKit(door=door, window=win, wall=wall,
                       wall_seam=tuple(int(c * 0.95) for c in wall), trim=trim, roof=roof,
                       eave_spread=6)


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
    if archetype not in ("town", "glade", "interior", "dungeon"):
        return {"ok": False, "error": "archetype must be one of town, glade, interior, dungeon"}
    seed = int(seed) if seed is not None else random.Random(scene_id).randint(0, 9999)
    w, h = _clamp(width_cells, 26), _clamp(height_cells, 18)
    pal = _palette(style)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    ground_rel = f"assets/{scene_id}_ground.png"
    json_rel = f"assets/{scene_id}_scene.json"

    if archetype == "town":
        img, data = _bake_town(w, h, seed, pal)
    elif archetype == "glade":
        img, data = _bake_glade(w, h, seed, pal)
    else:
        img, data = _bake_rooms(w, h, seed, pal, dark=(archetype == "dungeon"))

    img.save(root / ground_rel)
    data.update({"archetype": archetype, "seed": seed, "cell_px": CELL,
                 "width_cells": w, "height_cells": h, "ground": ground_rel})
    (root / json_rel).write_text(json.dumps(data), encoding="utf-8")
    return {"ok": True, "files": [ground_rel, json_rel],
            "note": f"scene built. Read {json_rel} for the walkable grid, door cells and points "
                    f"of interest; draw {ground_rel} as the map background, one cell = "
                    f"{CELL}px."}


def _walkable_strings(walk: np.ndarray):
    return ["".join("1" if c else "0" for c in row) for row in walk]


def _bake_town(w, h, seed, pal):
    grass, speck, road_c, plaza_c, *_ = pal
    lay = town_layout(w, h, seed)
    kit = _kit(pal)
    img, doors = compose_scene(lay, kit, cell=CELL, grass=grass, grass_speck=speck,
                               road_color=road_c, plaza_color=plaza_c)
    walk = np.ones((h, w), bool)
    for gx, gy, wc, hw in lay.buildings:
        walk[max(0, gy - 2):gy + hw, gx:gx + wc] = False
    pys, pxs = np.where(lay.plaza)
    plaza_center = [int(pxs.mean()), int(pys.mean())] if len(pxs) else [w // 2, h // 2]
    return img, {"doors": [list(d) for d in doors], "plaza_center": plaza_center,
                 "walkable": _walkable_strings(walk)}


def _bake_glade(w, h, seed, pal):
    grass, speck, *_ = pal
    pw, ph = w * CELL, h * CELL
    lay = glade_layout(pw, ph, seed)
    img_np = flat_speckle(ph, pw, grass, speck, stripe_period=CELL)
    img_np[~lay.clearing] = (img_np[~lay.clearing].astype(np.float32)
                             * np.array([0.5, 0.6, 0.5])).astype(np.uint8)
    water = np.array([70, 150, 190], np.uint8)
    img_np[lay.pond] = water
    img_np[band_inside(lay.pond, 14)] = (110, 190, 215)
    img_np[band_outside(lay.pond, 6) & lay.clearing] = (110, 86, 60)
    pil = Image.fromarray(img_np)
    cellgrid = lambda m: m.reshape(h, CELL, w, CELL).mean((1, 3)) > 0.5
    walk = cellgrid(lay.clearing & ~lay.pond)
    pys, pxs = np.where(lay.pond)
    pond_center = [int(pxs.mean()) // CELL, int(pys.mean()) // CELL] if len(pxs) else None
    return pil, {"doors": [], "pond_center": pond_center,
                 "walkable": _walkable_strings(walk)}


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
