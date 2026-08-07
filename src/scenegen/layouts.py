"""Scene archetypes: seeded layout generators over the SEMANTIC grid. The renderer paints what
these lay out; generation algorithms never touch pixels.

Towns are global-semantics objects, so the town generator is road-growth + lot placement (doors
face the road BY CONSTRUCTION), not a local-consistency solver — WFC-style approaches belong to
terrain fill, where local texture is the goal.
"""

import random
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np


@dataclass
class TownLayout:
    plaza: np.ndarray                  # bool cell grid
    road: np.ndarray                   # bool cell grid
    buildings: List[Tuple[int, int, int, int]] = field(default_factory=list)  # gx, gy, wc, hw


def town_layout(w: int, h: int, seed: int, max_buildings: int = 8) -> TownLayout:
    """Plaza at center, roads grown outward with turns and branches, building lots claimed along
    road frontage. Every lot satisfies: footprint and roof margin clear, and the cell south of the
    door IS road — so every door opens onto a walkable street."""
    rnd = random.Random(seed)
    road = np.zeros((h, w), bool)
    px, py = rnd.randint(w // 3, w * 2 // 3 - 4), rnd.randint(h // 3, h // 2)
    plaza = np.zeros((h, w), bool)
    plaza[py:py + 3, px:px + 4] = True

    starts = [(px - 1, py + 1, -1, 0), (px + 4, py + 1, 1, 0),
              (px + 1, py - 1, 0, -1), (px + 2, py + 3, 0, 1)]
    rnd.shuffle(starts)
    for sx, sy, dx, dy in starts[:rnd.randint(3, 4)]:
        x, y, ddx, ddy = sx, sy, dx, dy
        for _ in range(rnd.randint(12, 22)):
            if not (0 <= x < w and 0 <= y < h):
                break
            road[y, x] = True
            if rnd.random() < 0.12:
                if rnd.random() < 0.5:
                    ddx, ddy = ddy, ddx
                else:
                    bx, by, bdx, bdy = x, y, ddy, ddx
                    for _ in range(rnd.randint(6, 12)):
                        bx, by = bx + bdx, by + bdy
                        if 0 <= bx < w and 0 <= by < h:
                            road[by, bx] = True
            x, y = x + ddx, y + ddy
    road[py + 3, 1:w - 1] = True          # the east-west main street: south-facing doors need
    road &= ~plaza                        # horizontal frontage, and every town deserves one

    layout = TownLayout(plaza=plaza, road=road)
    blocked = plaza.copy()               # roads stay walkable; only lots and the plaza block lots
    frontage = [(x, y) for y in range(3, h - 1) for x in range(1, w - 1) if road[y, x]]
    rnd.shuffle(frontage)
    for x, y in frontage:
        wc = rnd.choice([2, 3, 3, 4, 5])
        hw = 2
        gx, gy = x - wc // 2, y - hw
        roof_margin = 2
        if gx < 1 or gx + wc >= w - 1 or gy - roof_margin < 0:
            continue
        margin = slice(gy - roof_margin, gy + hw), slice(gx - 1, gx + wc + 1)
        footprint = slice(gy, gy + hw), slice(gx, gx + wc)
        if blocked[margin].any() or road[footprint].any():
            continue
        if not road[gy + hw, gx + wc // 2]:
            continue
        blocked[gy - roof_margin:gy + hw, gx - 1:gx + wc + 1] = True
        layout.buildings.append((gx, gy, wc, hw))
        if len(layout.buildings) >= max_buildings:
            break
    return layout


@dataclass
class GladeLayout:
    clearing: np.ndarray               # bool pixel mask
    pond: np.ndarray                   # bool pixel mask


def glade_layout(pw: int, ph: int, seed: int) -> GladeLayout:
    """An organic clearing with an offset pond, as pixel masks (glades have no grid semantics
    beyond walkability, which the caller derives)."""
    from scipy import ndimage as ndi
    rnd = random.Random(seed)
    yy, xx = np.mgrid[0:ph, 0:pw]
    cx, cy = pw / 2, ph / 2
    ang = np.arctan2(yy - cy, xx - cx)
    blob = (((xx - cx) / (pw * 0.36)) ** 2 + ((yy - cy) / (ph * 0.38)) ** 2
            + 0.18 * np.sin(ang * 3 + rnd.uniform(0, 6))
            + 0.10 * np.sin(ang * 7 + rnd.uniform(0, 6))) < 1.0
    clearing = ndi.gaussian_filter(blob.astype(np.float32), 18) > 0.5
    px_, py_ = cx + rnd.uniform(-0.15, 0.15) * pw, cy + rnd.uniform(-0.1, 0.15) * ph
    pang = np.arctan2(yy - py_, xx - px_)
    pond = (((xx - px_) / (pw * 0.125)) ** 2 + ((yy - py_) / (ph * 0.125)) ** 2
            + 0.15 * np.sin(pang * 4 + rnd.uniform(0, 6))) < 1.0
    pond = ndi.gaussian_filter(pond.astype(np.float32), 12) > 0.5
    return GladeLayout(clearing=clearing, pond=pond & clearing)
