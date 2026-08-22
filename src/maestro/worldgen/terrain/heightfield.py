"""The composite height field (§2.2.3, eq. 6).

    H(x) = sum_r m~_r(x) [ h_r + sum_k w_rk N_rk(x) + sum_j alpha_rj G_rj(x) ]

Implemented literally. m~_r are the normalised region weights read out of the
layout map, N are noise bands, G are the geomorphic operators, and every
coefficient is the metre-denominated number the terrain plan gave.

No model is involved in this file and none should be. Everything here is a
function of the plan and a seed, which is what makes the refinement loop
possible later: an agent that wants a shallower canyon edits a number and the
world is rebuilt, rather than editing geometry that nothing can then re-derive.

Operators are placed inside their own region. A peak assigned to the northern
ridge that lands in the southern flats is not a peak, it is a mistake, so every
operator that has a position samples it from the region's own weight.
"""
from __future__ import annotations

import numpy as np

from .models import GeomorphOp, NoiseBand, RegionTerrain, TerrainPlan


def _rng(*parts: object) -> np.random.Generator:
    """A generator keyed by what it is for, so a rerun reproduces the world.

    Seeded by name rather than by call order: adding an operator to one region
    must not reshuffle the peaks of another.
    """
    seed = abs(hash("/".join(str(p) for p in parts))) % (2**32)
    return np.random.default_rng(seed)


def _grid(resolution: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel centres as fractions of the world, (x east, y south)."""
    yy, xx = np.mgrid[0:resolution, 0:resolution].astype(np.float32)
    return (xx + 0.5) / resolution, (yy + 0.5) / resolution


def fbm(
    cycles: float,
    resolution: int,
    octaves: int,
    generator: np.random.Generator,
) -> np.ndarray:
    """Fractal value noise in -1..1, `cycles` repeats across the world.

    Bicubic upsampling of a coarse lattice rather than gradient noise: at the
    wavelengths terrain is built from, the lattice is small and the difference
    is invisible, while the cost is a resize instead of a per-pixel dot product.
    """
    total = np.zeros((resolution, resolution), np.float32)
    amplitude, norm = 1.0, 0.0
    for octave in range(octaves):
        side = max(2, int(cycles * (2 ** octave)) + 1)
        lattice = generator.random((side, side)).astype(np.float32)
        # np.kron-free bicubic: PIL is already a dependency and does it properly
        from PIL import Image

        # mode "F" keeps the lattice float: through uint8 it has 256 levels, and bicubic
        # over 256 levels leaves flat shelves the whole slope then shows as contour rings
        upsampled = np.clip(np.asarray(
            Image.fromarray(lattice, mode="F").resize(
                (resolution, resolution), Image.BICUBIC
            ),
            np.float32,
        ), 0.0, 1.0)
        total += (upsampled - 0.5) * 2.0 * amplitude
        norm += amplitude
        amplitude *= 0.5
    return total / max(norm, 1e-6)


def noise_field(
    band: NoiseBand,
    region_id: str,
    index: int,
    resolution: int,
    size_m: float,
) -> np.ndarray:
    """One band N_rk scaled by its own amplitude, in metres."""
    cycles = max(1.0, size_m / max(band.wavelength_m, 1e-3))
    generator = _rng("noise", region_id, index, band.wavelength_m)
    return fbm(cycles, resolution, band.octaves, generator) * band.amplitude_m


def _sample_positions(
    weight: np.ndarray,
    count: int,
    generator: np.random.Generator,
) -> list[tuple[float, float]]:
    """`count` positions drawn from inside a region, as fractions of the world."""
    resolution = weight.shape[0]
    flat = np.clip(weight.reshape(-1), 0.0, None)
    if flat.sum() <= 0:
        return []
    picked = generator.choice(flat.size, size=count, p=flat / flat.sum(), replace=True)
    rows, cols = np.divmod(picked, resolution)
    return [
        ((c + 0.5) / resolution, (r + 0.5) / resolution) for r, c in zip(rows, cols)
    ]


def operator_field(
    op: GeomorphOp,
    region_id: str,
    index: int,
    weight: np.ndarray,
    resolution: int,
    size_m: float,
) -> np.ndarray:
    """One operator G_rj scaled by its relief, in metres.

    Each kind is a closed-form function of position. They are deliberately
    simple: what makes terrain read as terrain is the composition of several of
    these under noise, not the sophistication of any one.
    """
    xx, yy = _grid(resolution)
    generator = _rng("op", region_id, index, op.kind)
    relief = op.relief_m
    feature = (op.feature_size_m or size_m * 0.25) / size_m  # in world fractions
    angle = np.deg2rad(op.orientation_deg or 0.0)
    # distance along and across the operator's own axis
    across = (xx - 0.5) * np.cos(angle) + (yy - 0.5) * np.sin(angle)
    field = np.zeros((resolution, resolution), np.float32)

    if op.kind in ("peak", "crater"):
        for centre in _sample_positions(weight, op.count or 3, generator):
            radius = max(feature / 2, 1e-3)
            distance = np.sqrt((xx - centre[0]) ** 2 + (yy - centre[1]) ** 2) / radius
            if op.kind == "peak":
                field += np.exp(-distance ** 2 * 2.5)
            else:
                # bowl with a raised rim: the rim is what makes a crater legible
                # from the ground rather than reading as a dent
                bowl = -np.exp(-distance ** 2 * 3.0)
                rim = 0.45 * np.exp(-((distance - 1.0) ** 2) * 9.0)
                field += bowl + rim
    elif op.kind == "ridge":
        field = 1.0 - np.abs(np.clip(across / max(feature, 1e-3), -1.0, 1.0))
        field = field ** 1.5  # a crest, not a roof
    elif op.kind == "dune":
        wavelength = max(feature, 1e-3)
        phase = 2.0 * np.pi * across / wavelength
        # asymmetric: a long windward slope and a short steep slip face
        wave = np.sin(phase)
        field = (wave + 0.35 * np.sin(2.0 * phase)) * 0.5 + 0.5
    elif op.kind == "terrace":
        steps = op.steps or 5
        ramp = np.clip((yy * np.cos(angle) - xx * np.sin(angle)) + 0.5, 0.0, 1.0)
        field = np.floor(ramp * steps) / max(steps - 1, 1)
    elif op.kind == "plateau":
        centres = _sample_positions(weight, 1, generator) or [(0.5, 0.5)]
        radius = max(feature, 1e-3)
        distance = np.sqrt((xx - centres[0][0]) ** 2 + (yy - centres[0][1]) ** 2) / radius
        # flat on top, steep at the edge: smoothstep run backwards
        edge = np.clip((1.15 - distance) / 0.30, 0.0, 1.0)
        field = edge * edge * (3.0 - 2.0 * edge)
    elif op.kind in ("valley", "canyon"):
        width = max(feature / 2, 1e-3)
        wander = 0.08 * np.sin(2.0 * np.pi * (xx * np.sin(angle) + yy * np.cos(angle)))
        distance = np.abs(across + wander) / width
        if op.kind == "valley":
            field = -np.exp(-distance ** 2 * 1.6)
        else:
            # steep walls and a flat floor: a canyon is a slot, not a trough
            field = -np.clip(1.6 - distance ** 3, 0.0, 1.0)
    elif op.kind == "erosion":
        # drainage-like: carve where a noise field is high, leaving branching
        # channels, and take height away rather than adding it
        channels = np.abs(fbm(6.0, resolution, 4, generator))
        field = -(1.0 - np.clip(channels * 2.2, 0.0, 1.0))
    return (field * relief).astype(np.float32)


def region_field(
    region: RegionTerrain,
    weight: np.ndarray,
    resolution: int,
    size_m: float,
) -> np.ndarray:
    """The bracketed term of eq. 6 for one region, in metres."""
    field = np.full((resolution, resolution), region.base_elevation_m, np.float32)
    for index, band in enumerate(region.noise):
        field += noise_field(band, region.region_id, index, resolution, size_m)
    for index, op in enumerate(region.operators):
        field += operator_field(op, region.region_id, index, weight, resolution, size_m)
    return field


def build(plan: TerrainPlan, weights: np.ndarray) -> np.ndarray:
    """H(x) for the whole world, in metres.

    `weights` is (regions, n, n) in the order of `plan.layout` — the same
    partition the materials and the scattering use, which is the entire point of
    reading it once and sharing it.
    """
    resolution = weights.shape[-1]
    size_m = plan.world.size_m
    height = np.zeros((resolution, resolution), np.float32)
    for index, row in enumerate(plan.layout):
        region = plan.terrain_for(row.region_id)
        height += weights[index] * region_field(region, weights[index], resolution, size_m)

    return height


def slope_deg(height: np.ndarray, size_m: float) -> np.ndarray:
    """Ground slope in degrees, from the height field's own gradient."""
    metres_per_pixel = size_m / height.shape[0]
    dy, dx = np.gradient(height.astype(np.float32), metres_per_pixel)
    return np.degrees(np.arctan(np.sqrt(dx * dx + dy * dy)))


def normals(height: np.ndarray, size_m: float) -> np.ndarray:
    """Unit surface normals, (n, n, 3), y up in the engine's sense."""
    metres_per_pixel = size_m / height.shape[0]
    dy, dx = np.gradient(height.astype(np.float32), metres_per_pixel)
    normal = np.stack([-dx, np.ones_like(height), -dy], -1)
    return normal / np.linalg.norm(normal, axis=-1, keepdims=True)


__all__ = [
    "build",
    "region_field",
    "operator_field",
    "noise_field",
    "fbm",
    "slope_deg",
    "normals",
]
