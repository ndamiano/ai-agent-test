"""Zone scatter: decorations as declarative rules — sprite × zone mask × count × spacing — with
one shared occupancy so rules never collide. Hand-picked coordinates are for POIs only."""

import random
from typing import List, Tuple

import numpy as np
from PIL import Image

from scenegen.compose import paste_with_shadow


class Scatterer:
    def __init__(self, pil: Image.Image, seed: int):
        self.pil = pil
        self.rnd = random.Random(seed)
        self.occupied: List[Tuple[int, int]] = []

    def scatter(self, sprite: Image.Image, zone: np.ndarray, count: int, spacing: int,
                jitter: int = 10, shadow: bool = True) -> int:
        ys, xs = np.where(zone)
        pts = list(zip(xs.tolist(), ys.tolist()))
        self.rnd.shuffle(pts)
        placed = 0
        for x, y in pts:
            if placed >= count:
                break
            if all((x - a) ** 2 + (y - b) ** 2 > spacing * spacing for a, b in self.occupied):
                self.occupied.append((x, y))
                paste_with_shadow(self.pil, sprite,
                                  x + self.rnd.randint(-jitter, jitter),
                                  y + self.rnd.randint(-jitter, jitter), shadow=shadow)
                placed += 1
        return placed

    def poi(self, sprite: Image.Image, x: int, y: int, shadow: bool = True) -> None:
        self.occupied.append((x, y))
        paste_with_shadow(self.pil, sprite, x, y, shadow=shadow)
