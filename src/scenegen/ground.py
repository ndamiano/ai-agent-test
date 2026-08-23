"""Ground layers: a quiet field, distance bands, cell masks.

Ground is the backdrop the subjects must read against, so it stays QUIET — flat color with
sparse deliberate marks; a bright painterly field shouts over every sprite on it.
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


def band_inside(mask: np.ndarray, depth: float) -> np.ndarray:
    """The strip of `mask` within `depth` px of its edge — shore shallows, carpet trim."""
    return mask & (ndi.distance_transform_edt(mask) < depth)


def cell_mask(grid: np.ndarray, cell: int) -> np.ndarray:
    """Expand a boolean cell grid to a pixel mask."""
    return np.kron(grid, np.ones((cell, cell), bool))
