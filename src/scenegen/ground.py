"""Ground layers: quiet fields, procedural regular-structure materials, organic masks and
distance bands.

The usability law this file exists for: ground is the backdrop the subjects must read against, so
it stays QUIET — flat color with sparse deliberate marks. Rich textures (quilted exemplars) are
for muted or dark surfaces only; a bright painterly field shouts over every sprite on it.
Regular-structure materials (planks, checker, flagstone) are drawn, not diffused — a plank grid
with per-plank jitter beats any sampler's parquet.
"""

from typing import Tuple

import numpy as np
from scipy import ndimage as ndi

Color = Tuple[int, int, int]


def flat_speckle(h: int, w: int, base: Color, speck: Color, stripe_period: int = 0) -> np.ndarray:
    """A quiet field: flat base, sparse two-tone speckle, optional faint row striping."""
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = base
    marks = ((xx * 7 + yy * 13) % 97 < 3) | ((xx * 11 + yy * 5) % 89 < 2)
    img[marks] = speck
    if stripe_period:
        rows = (yy // stripe_period) % 2 == 0
        img[rows] = (img[rows].astype(np.float32) * 0.97).astype(np.uint8)
    return img


def planks(h: int, w: int, base: Color, plank_w: int = 96, plank_h: int = 24) -> np.ndarray:
    """Brick-bond planks with per-plank brightness/hue jitter and seam lines."""
    yy, xx = np.mgrid[0:h, 0:w]
    row = yy // plank_h
    offs = (row % 2) * (plank_w // 2)
    pid = ((xx + offs) // plank_w) * 7919 + row * 104729
    bright = np.abs(np.sin(pid * 0.0001)) * 0.22 + 0.78
    hue = np.abs(np.sin(pid * 0.00037)) * 0.12
    grain = np.sin(xx * 0.35 + pid % 7) * 3
    r, g, b = base
    img = np.stack([(r + grain) * bright * (1 + hue * 0.3),
                    (g + grain) * bright,
                    (b + grain) * bright * (1 - hue * 0.3)], -1)
    seam = (((xx + offs) % plank_w) < 2) | ((yy % plank_h) < 2)
    return np.where(seam[..., None], img * 0.55, img).clip(0, 255).astype(np.uint8)


def checker(h: int, w: int, light: Color, dark: Color, cell: int = 64) -> np.ndarray:
    """Checkered floor with a soft sine wobble standing in for veining."""
    yy, xx = np.mgrid[0:h, 0:w]
    noise = (np.sin(xx * 0.05 + yy * 0.031) * 4 + np.sin(xx * 0.013 - yy * 0.021) * 3)
    a = np.stack([light[0] + noise, light[1] + noise, light[2] + noise], -1)
    b = np.stack([dark[0] + noise, dark[1] + noise, dark[2] + noise], -1)
    on = ((xx // cell) + (yy // cell)) % 2 == 0
    return np.where(on[..., None], a, b).clip(0, 255).astype(np.uint8)


def tilefill(texture: np.ndarray, h: int, w: int) -> np.ndarray:
    reps = (h // texture.shape[0] + 1, w // texture.shape[1] + 1)
    return np.tile(texture, (reps[0], reps[1], 1))[:h, :w]


def organic(mask: np.ndarray, radius: float = 12.0) -> np.ndarray:
    """Round a blocky boolean mask into an organic blob (blur + threshold)."""
    return ndi.gaussian_filter(mask.astype(np.float32), radius) > 0.5


def band_inside(mask: np.ndarray, depth: float) -> np.ndarray:
    """The strip of `mask` within `depth` px of its edge — shore shallows, carpet trim."""
    return mask & (ndi.distance_transform_edt(mask) < depth)


def band_outside(mask: np.ndarray, depth: float) -> np.ndarray:
    """The strip just outside `mask` — banks, rims, wall-base shadows."""
    return (~mask) & (ndi.distance_transform_edt(~mask) < depth)


def cell_mask(grid: np.ndarray, cell: int) -> np.ndarray:
    """Expand a boolean cell grid to a pixel mask."""
    return np.kron(grid, np.ones((cell, cell), bool))
