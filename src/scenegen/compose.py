"""Scene assembly: painter's-order pasting with grounded shadows, and the town composer that
strings the layers together — ground, roads, buildings, scatter, light — from a layout + kits."""

from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from scenegen import ground as G
from scenegen.kit import BuildingKit, assemble
from scenegen.layouts import TownLayout
from scenegen.light import Pool, apply_lights


def paste_with_shadow(pil: Image.Image, sprite: Image.Image, cx: int, cy: int,
                      shadow: bool = True) -> None:
    if shadow:
        sh = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
        sh.putalpha(sprite.split()[3].point(lambda v: int(v * 0.35)))
        pil.paste(sh, (int(cx - sprite.width / 2) + 3, int(cy - sprite.height / 2) + 6), sh)
    pil.paste(sprite, (int(cx - sprite.width / 2), int(cy - sprite.height / 2)), sprite)


def compose_scene(layout: TownLayout, kit: BuildingKit, cell: int = 48,
                  grass: Tuple[int, int, int] = (118, 178, 84),
                  grass_speck: Tuple[int, int, int] = (106, 164, 74),
                  road_color: Tuple[int, int, int] = (228, 214, 166),
                  plaza_color: Tuple[int, int, int] = (202, 196, 182),
                  pools: Optional[List[Pool]] = None,
                  ambient: Tuple[float, float, float] = (1.02, 1.01, 0.98),
                  ambient_level: float = 1.0,
                  ) -> Tuple[Image.Image, List[Tuple[int, int]]]:
    """Layout + kit -> (image, door cells). The caller scatters decorations on the returned image
    (Scatterer) before lighting if it wants them inside the grade; this composer applies lights
    last over whatever is on the canvas."""
    h, w = layout.road.shape
    ph, pw = h * cell, w * cell
    img = G.flat_speckle(ph, pw, grass, grass_speck, stripe_period=cell)
    road_px = G.cell_mask(layout.road, cell)
    img[road_px] = road_color
    yy, xx = np.mgrid[0:ph, 0:pw]
    img[((xx * 3 + yy * 7) % 53 < 2) & road_px] = tuple(int(c * 0.93) for c in road_color)
    plaza_px = G.cell_mask(layout.plaza, cell)
    img[plaza_px] = plaza_color
    img[(((xx % cell < 2) | (yy % cell < 2)) & plaza_px)] = \
        tuple(int(c * 0.92) for c in plaza_color)

    pil = Image.fromarray(img)
    doors = []
    for gx, gy, wc, hw in sorted(layout.buildings, key=lambda b: b[1]):
        doors.append(assemble(pil, kit, gx, gy, wc, hw, cell=cell))
    if pools is not None:
        out = apply_lights(np.asarray(pil), pools, ambient=ambient, ambient_level=ambient_level)
        pil = Image.fromarray(out)
    return pil, doors


def zone_masks(layout: TownLayout, cell: int = 48) -> Dict[str, np.ndarray]:
    """The standard decoration zones, derived from the layout: along_roads, against_walls,
    open_ground, plaza_rim. Building footprints (with roof margin) are excluded everywhere."""
    from scipy import ndimage as ndi
    h, w = layout.road.shape
    road_px = G.cell_mask(layout.road | layout.plaza, cell)
    bmask = np.zeros((h * cell, w * cell), bool)
    for gx, gy, wc, hw in layout.buildings:
        bmask[max(0, (gy - 2) * cell):(gy + hw) * cell,
              max(0, (gx - 1) * cell):(gx + wc + 1) * cell] = True
    plaza_px = G.cell_mask(layout.plaza, cell)
    return {
        "along_roads": (~road_px) & ndi.binary_dilation(road_px, iterations=14) & ~bmask,
        "against_walls": bmask & (~road_px) & ndi.binary_dilation(road_px, iterations=20),
        "open_ground": (~road_px) & ~bmask
                       & (ndi.distance_transform_edt(~road_px) > 60),
        "plaza_rim": (~plaza_px) & ndi.binary_dilation(plaza_px, iterations=10) & ~bmask,
    }
