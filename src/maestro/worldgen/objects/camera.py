"""The regional terrain camera, kappa_r = (K_t, E_t) (§2.3.2).

Everything in this stage rests on one fact: the composition image is generated
FROM a render of terrain we built, so its camera is not estimated, it is chosen.
Every pixel of the composition is a known ray into a known world, and that is
what makes eq. 13 recoverable at all.

Which means this module has exactly one job it cannot get wrong: the rays it
casts must be the rays the renderer drew. The three.js conventions are therefore
reproduced here rather than approximated —

    right-handed, +Y up, the camera looking down its own -Z,
    fov is the VERTICAL field of view,
    the basis comes from lookAt(target) with the camera's own up axis.

and the height field is sampled the way the loader samples it, with
height[row, col] at world x = col/resolution * size_m, z = row/resolution * size_m.

A camera written here goes to the renderer as a shot dictionary and stays here as
intrinsics; nothing re-derives either from the other.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..terrain.models import RegionLayout, TerrainPlan

# Framing defaults. A region is shot from outside itself looking in, high enough
# that the ground reads as ground rather than as a horizon line, and not so high
# that it becomes a map — objects have to sit ON something visible.
ELEVATION_DEG = 28.0
FOV_DEG = 50.0

# How much of the frame the largest object should span. This, not the size of
# the region, is what sets how far back the camera stands: the segmenter that
# has to find the object again needs it to be roughly a tenth of the frame, and
# a view framed to hold a whole 300 m region puts a 4 m jeep at eleven pixels.
# A region view is a place you are standing in, not a map of the region.
OBJECT_FRACTION = 0.16
MIN_FRAME_M = 12.0
MAX_FRAME_M = 160.0


@dataclass(frozen=True)
class Camera:
    """kappa_r: where the camera is, what it looks at, and how it projects."""

    position: tuple[float, float, float]
    target: tuple[float, float, float]
    fov_deg: float
    width: int
    height: int

    # -- E_t -----------------------------------------------------------------

    @property
    def basis(self) -> np.ndarray:
        """Columns (x, y, z) of the camera basis in world space, as the renderer builds it.

        The camera looks down -z, so z points back towards the viewer. When the
        view is parallel to UP the up vector falls back to forward, which is what
        the render page does; without it a top-down shot cannot build a basis at
        all and returns a picture of somewhere else.
        """
        position = np.asarray(self.position, dtype=float)
        target = np.asarray(self.target, dtype=float)
        direction = target - position
        direction /= np.linalg.norm(direction)
        up = np.array([0.0, 1.0, 0.0])
        if abs(float(direction @ up)) > 0.999:
            up = np.array([0.0, 0.0, -1.0])  # forward, the render page's fallback up
        z = -direction
        x = np.cross(up, z)
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        return np.stack([x, y, z], axis=1)

    # -- K_t -----------------------------------------------------------------

    @property
    def focal_px(self) -> float:
        """Focal length in pixels. Square pixels, so one number covers both axes."""
        return (self.height * 0.5) / np.tan(np.deg2rad(self.fov_deg) * 0.5)

    @property
    def intrinsics(self) -> np.ndarray:
        """K_t, the 3x3 pinhole matrix this render corresponds to."""
        f = self.focal_px
        return np.array([
            [f, 0.0, self.width * 0.5],
            [0.0, f, self.height * 0.5],
            [0.0, 0.0, 1.0],
        ])

    def ray(self, px: float, py: float) -> np.ndarray:
        """Unit direction in world space through pixel `(px, py)`.

        Pixel centres, and +y down the image, which is what every mask this
        stage reads is indexed by.
        """
        f = self.focal_px
        camera_space = np.array([
            (px + 0.5 - self.width * 0.5) / f,
            -(py + 0.5 - self.height * 0.5) / f,
            -1.0,
        ])
        world = self.basis @ camera_space
        return world / np.linalg.norm(world)

    def project(self, point: np.ndarray) -> tuple[float, float, float]:
        """Pixel and depth of a world point. Depth is along the view axis."""
        relative = np.asarray(point, dtype=float) - np.asarray(self.position, dtype=float)
        local = self.basis.T @ relative
        depth = -float(local[2])
        if depth <= 1e-6:
            return float("nan"), float("nan"), depth
        f = self.focal_px
        return (
            float(local[0] / depth * f + self.width * 0.5 - 0.5),
            float(-local[1] / depth * f + self.height * 0.5 - 0.5),
            depth,
        )

    def shot(self, name: str) -> dict:
        """This camera as the shot entry the renderer reads."""
        return {
            "name": name,
            "position": list(self.position),
            "look_at": list(self.target),
            "fov": self.fov_deg,
        }

    def to_dict(self) -> dict:
        return {
            "position": list(self.position),
            "target": list(self.target),
            "fov_deg": self.fov_deg,
            "width": self.width,
            "height": self.height,
            "focal_px": self.focal_px,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Camera:
        return cls(
            position=tuple(data["position"]),
            target=tuple(data["target"]),
            fov_deg=float(data["fov_deg"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )


# -- sampling the world ------------------------------------------------------


def sample_height(height: np.ndarray, size_m: float, x: float, z: float) -> float:
    """Ground height at world (x, z), bilinear, clamped at the edges."""
    resolution = height.shape[0]
    fx = np.clip(x / size_m * resolution, 0.0, resolution - 1.0)
    fz = np.clip(z / size_m * resolution, 0.0, resolution - 1.0)
    x0, z0 = int(fx), int(fz)
    x1, z1 = min(x0 + 1, resolution - 1), min(z0 + 1, resolution - 1)
    tx, tz = fx - x0, fz - z0
    top = height[z0, x0] * (1 - tx) + height[z0, x1] * tx
    bottom = height[z1, x0] * (1 - tx) + height[z1, x1] * tx
    return float(top * (1 - tz) + bottom * tz)


def cast(
    camera: Camera,
    height: np.ndarray,
    size_m: float,
    px: float,
    py: float,
    *,
    step_m: float = 0.5,
    max_distance: float | None = None,
) -> tuple[np.ndarray, float] | None:
    """First intersection of the ray through `(px, py)` with the ground.

    Returns the world point and its depth along the view axis, or None if the
    ray leaves the world without hitting anything — which is a real answer for
    any pixel above the horizon.

    Marched rather than solved, because the ground is a height field and not a
    surface with a closed form, then bisected once a step crosses it. The step
    is small relative to the terrain's own feature size; a coarse march skips
    over ridges and lands the object on the valley behind them.
    """
    origin = np.asarray(camera.position, dtype=float)
    direction = camera.ray(px, py)
    limit = max_distance if max_distance is not None else size_m * 3.0

    def gap(distance: float) -> float:
        point = origin + direction * distance
        return point[1] - sample_height(height, size_m, point[0], point[2])

    distance = 0.0
    previous = gap(0.0)
    if previous <= 0.0:
        # the camera itself is below ground; nothing sensible to report
        return None
    while distance < limit:
        distance += step_m
        current = gap(distance)
        if current <= 0.0:
            low, high = distance - step_m, distance
            for _ in range(24):
                middle = (low + high) * 0.5
                if gap(middle) > 0.0:
                    low = middle
                else:
                    high = middle
            hit = origin + direction * ((low + high) * 0.5)
            return hit, camera.project(hit)[2]
        previous = current
    return None


# -- framing a region --------------------------------------------------------


def frame_width_for(sizes_m: list[float]) -> float:
    """How wide the view should be, in metres, to hold objects of these sizes.

    Driven by the largest, because it is the one that overflows, and floored so
    that a region of small things is not shot from two metres away. Clamped at
    the top because past a point the view stops being a place and starts being
    a survey.
    """
    largest = max(sizes_m) if sizes_m else MIN_FRAME_M
    return float(np.clip(largest / OBJECT_FRACTION, MIN_FRAME_M, MAX_FRAME_M))


def region_camera(
    plan: TerrainPlan,
    height: np.ndarray,
    region_id: str,
    frame_width_m: float,
    *,
    width: int = 1216,
    height_px: int = 832,
    fov_deg: float = FOV_DEG,
    elevation_deg: float = ELEVATION_DEG,
) -> Camera:
    """Frame `frame_width_m` metres of ground at the region's centre.

    The camera stands within the world looking across the region, away from the
    map's centre so that what lies beyond the objects is the rest of the world
    rather than the map's edge. It is close: close enough that the objects it is
    about will survive segmentation, which means most of the region is out of
    shot. That is correct. The composition is a view of a place, and the region
    is where the place is.
    """
    layout = _layout_for(plan, region_id)
    size = plan.world.size_m
    centre_x = layout.center[0] * size
    centre_z = layout.center[1] * size
    ground = sample_height(height, size, centre_x, centre_z)

    # far enough back that frame_width_m spans the image horizontally
    aspect = width / height_px
    half_horizontal = aspect * np.tan(np.deg2rad(fov_deg) * 0.5)
    distance = (frame_width_m * 0.5) / half_horizontal
    elevation = np.deg2rad(elevation_deg)

    offset_x = centre_x - size * 0.5
    offset_z = centre_z - size * 0.5
    if abs(offset_x) < 1e-6 and abs(offset_z) < 1e-6:
        offset_x, offset_z = 0.0, 1.0  # a region at the centre still needs a side
    length = float(np.hypot(offset_x, offset_z))
    away_x, away_z = offset_x / length, offset_z / length

    horizontal = distance * np.cos(elevation)
    return Camera(
        position=(
            float(centre_x + away_x * horizontal),
            float(ground + distance * np.sin(elevation)),
            float(centre_z + away_z * horizontal),
        ),
        target=(float(centre_x), float(ground), float(centre_z)),
        fov_deg=fov_deg,
        width=width,
        height=height_px,
    )


def _layout_for(plan: TerrainPlan, region_id: str) -> RegionLayout:
    for row in plan.layout:
        if row.region_id == region_id:
            return row
    raise KeyError(f"no region {region_id!r} in the terrain plan")


__all__ = ["Camera", "region_camera", "frame_width_for", "cast", "sample_height"]
