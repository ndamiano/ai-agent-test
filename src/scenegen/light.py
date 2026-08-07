"""The light plan: pools with a temperature, a grade, a vignette. Light IMPLIES its fixture —
a chandelier is a warm pool on the floor, never a drawn object (drawn ones read as floor
medallions and add noise). Warm pools against cold window-light is most of "atmosphere"."""

from typing import List, Tuple

import numpy as np

WARM = (1.20, 1.09, 0.88)
COLD = (0.85, 0.95, 1.25)
FIRE = (1.45, 1.12, 0.80)

Pool = Tuple[float, float, float, Tuple[float, float, float]]   # x, y, radius, tint


def apply_lights(base: np.ndarray, pools: List[Pool],
                 ambient: Tuple[float, float, float] = (1.0, 1.0, 1.0),
                 ambient_level: float = 1.0,
                 glow_cap: float = 0.5,
                 vignette: float = 0.22) -> np.ndarray:
    """base HxWx3 uint8 -> graded uint8. Pools never blow out (glow_cap); the grade multiplies
    everything, so a night scene is ambient_level ~0.85 with a cool ambient tint."""
    h, w = base.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    b = base.astype(np.float32)
    graded = b * np.array(ambient) * ambient_level
    glow = np.zeros((h, w, 3), np.float32)
    for lx, ly, r, tint in pools:
        y0, y1 = max(0, int(ly - r)), min(h, int(ly + r))
        x0, x1 = max(0, int(lx - r)), min(w, int(lx + r))
        if y0 >= y1 or x0 >= x1:
            continue
        gy, gx = np.mgrid[y0:y1, x0:x1]
        d = np.sqrt((gx - lx) ** 2 + (gy - ly) ** 2)
        g = ((1 - d / r).clip(0, 1) ** 2)[..., None] * np.array(tint)
        glow[y0:y1, x0:x1] = np.maximum(glow[y0:y1, x0:x1], g)
    glow = glow.clip(0, glow_cap)
    out = graded * (1 - glow.max(-1, keepdims=True)) + b * glow
    cy, cx = h / 2, w / 2
    vig = 1 - vignette * (((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2).clip(0, 1)
    return (out * vig[..., None]).clip(0, 255).astype(np.uint8)
