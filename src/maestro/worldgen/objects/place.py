"""Object placement — T_place^i (§2.3.2, eq. 11-13).

The composition was generated from a render of terrain we built, so the camera
that took it is known exactly and every instance pixel is a ray into the world.
Placement is recovering, for each instance, where along its ray the object sits
and how big it is.

The paper pairs two rays: one from the object reconstruction camera into the
reconstructed mesh, one from the terrain camera into the terrain, and takes the
scale from their focal lengths and depths,

    s_i = (Z_t / Z_o) * (f_i^o / f_hat_i)                          [eq. 12]

SAM3D reports its own reconstruction camera, which is what makes that ratio
computable. TRELLIS2 does not: it returns a mesh normalised into a unit box with
no camera and no pose. The ratio is therefore not available, and pretending
otherwise would mean inventing f_i^o.

What replaces it is the same quantity measured on the other side. An instance
subtending w pixels at depth Z_t through focal f spans

    width_world = w * Z_t / f

metres, and the mesh is scaled so its own longest horizontal dimension matches.
Where the paper transfers apparent size between two cameras, this reads apparent
size off the one camera it has. Same answer, one less camera — and it keeps the
property that matters, which is that the object ends up the size it looks.

The anchor is the bottom of the box rather than its centre. The paper's centre
ray works because it intersects the reconstructed mesh to find P_o at the same
pixel; without that mesh-side intersection, a centre ray through a tall object
lands on terrain well behind where the object stands. The bottom of the box is
where the object meets the ground, which is the point we actually want.

Then the contact search from the paper, kept as-is in spirit: slide the anchor
along the camera ray and rescale together so the 2D projection is preserved --
the object stays exactly where the image put it -- and keep the depth at which
the object sits on the ground rather than through it or above it.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from .camera import Camera, cast, sample_height

# How far the search may move an object along its ray, as a fraction of the
# depth the anchor ray found. Small: the ray is right, the depth along it is
# what single-view geometry gets wrong, and a wide search will happily park a
# jeep on a different landform.
SEARCH_RANGE = 0.22
SEARCH_STEPS = 29

# How much the image is allowed to say about size. The box's extent is a
# measurement of the box, and boxes include ground, shadow and whatever else
# leaked in, so apparent size systematically overreads -- on the jungle town a
# bench measured 5.5 m and a barrel 3.2 m. The planner never saw the picture but
# does know roughly how big a bench is, so it anchors and the measurement
# nudges: blended in log space at this weight, then clamped so the image can
# never move it by more than the ratio below.
TO_PLANNER = 0.75
SIZE_CLAMP = 1.35

CONTACT_TOLERANCE_M = 0.08  # of the object's own height


def glb_bounds(path: Path | str) -> tuple[np.ndarray, np.ndarray]:
    """Axis-aligned bounds of a GLB's geometry, from its accessors.

    glTF requires min and max on POSITION accessors, so the bounding box is
    readable from the JSON chunk alone -- no buffer decoding, no mesh library,
    and no cost proportional to the size of the mesh.
    """
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{path} is not a GLB")
    length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:  # 'JSON'
        raise ValueError(f"{path}: first chunk is not JSON")
    document = json.loads(data[20:20 + length].decode("utf-8"))

    lows, highs = [], []
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            index = primitive.get("attributes", {}).get("POSITION")
            if index is None:
                continue
            accessor = document["accessors"][index]
            if "min" in accessor and "max" in accessor:
                lows.append(accessor["min"][:3])
                highs.append(accessor["max"][:3])
    if not lows:
        raise ValueError(f"{path}: no POSITION accessor with bounds")
    return np.min(np.array(lows), axis=0), np.max(np.array(highs), axis=0)


def anchor_pixel(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
    """Where the object meets the ground, in image coordinates.

    The bottom edge of the box, at its horizontal centre. This was the bottom of
    the segmentation mask until §2.3 stopped segmenting; over every instance of
    the cliff city the two definitions sat 0.28 m apart in the median, which is
    below what the size blend below is doing to the object anyway.
    """
    x0, y0, x1, y1 = bbox
    return (x0 + x1) * 0.5, float(y1)


def place_instance(
    row: dict,
    camera: Camera,
    height: np.ndarray,
    size_m: float,
    mesh: Path | str | None,
) -> dict | None:
    """Recover one object's position, scale and orientation in world space.

    Returns None when the instance's ray never meets the ground, which happens
    for anything the composition drew above the horizon.
    """
    px, py = anchor_pixel(row["bbox"])
    hit = cast(camera, height, size_m, px, py)
    if hit is None:
        return None
    point, depth = hit

    # apparent size -> world size, at the depth the ray found
    metres_per_px = depth / camera.focal_px
    measured_m = float(row["longest_px"]) * metres_per_px
    # nobody planned this category and the vision model would not guess a size:
    # the image is then the only evidence there is
    stated_m = float(row["typical_size_m"] or measured_m)
    blended = float(np.exp(
        TO_PLANNER * np.log(max(stated_m, 1e-3))
        + (1.0 - TO_PLANNER) * np.log(max(measured_m, 1e-3))
    ))
    world_m = float(
        np.clip(blended, stated_m / SIZE_CLAMP, stated_m * SIZE_CLAMP)
    )

    bounds = None
    if mesh is not None and Path(mesh).exists():
        low, high = glb_bounds(mesh)
        bounds = (low, high)
        extent = high - low
        longest = float(max(extent[0], extent[2])) or 1.0
        object_height_m = float(extent[1]) * (world_m / longest)
    else:
        object_height_m = world_m * 0.5

    # yaw: TRELLIS2 reconstructs facing the view it was given, so the mesh is
    # turned to face the camera that saw it. Single-view reconstruction cannot
    # recover the true heading, and this is the one choice that is right by
    # construction rather than a guess.
    direction = np.asarray(camera.position) - np.asarray(camera.target)
    yaw_deg = float(np.degrees(np.arctan2(direction[0], direction[2])))

    seated = _seat(
        camera, height, size_m, px, py, depth, world_m, object_height_m, bounds
    )
    return {
        "index": row["index"],
        "category": row["category"],
        "position": [float(v) for v in seated["position"]],
        "yaw_deg": yaw_deg,
        # size is reported as the object's HEIGHT in metres, because that is
        # what the renderer scales by -- it fits a mesh to a height and seats
        # its base on the ground. Reporting the horizontal extent it was
        # measured from as well, since that is the quantity the image gave us.
        "height_m": float(object_height_m * seated["ratio"]),
        "size_m": float(world_m * seated["ratio"]),
        "measured_m": measured_m,
        "stated_m": stated_m,
        "clamped": bool(abs(measured_m - world_m) > 1e-6),
        "depth_m": float(depth * seated["ratio"]),
        "contact": seated["contact"],
        "anchor_px": [px, py],
    }


def _seat(
    camera: Camera,
    height: np.ndarray,
    size_m: float,
    px: float,
    py: float,
    depth: float,
    world_m: float,
    object_height_m: float,
    bounds: tuple[np.ndarray, np.ndarray] | None,
) -> dict:
    """Slide along the ray, preserving projection, and keep the best contact.

    Moving the anchor to t times its depth and scaling the object by t leaves
    the image unchanged, so the whole family of candidates looks identical from
    the camera and only their relationship to the ground differs. The one that
    sits on the ground wins.
    """
    origin = np.asarray(camera.position, dtype=float)
    direction = camera.ray(px, py)
    best = None
    for ratio in np.linspace(1.0 - SEARCH_RANGE, 1.0 + SEARCH_RANGE, SEARCH_STEPS):
        point = origin + direction * (depth / _forward(camera, direction) * ratio)
        ground = sample_height(height, size_m, point[0], point[2])
        clearance = float(point[1] - ground)
        footprint = _footprint_error(
            height, size_m, point, world_m * ratio, ground
        )
        tolerance = max(CONTACT_TOLERANCE_M * object_height_m * ratio, 0.02)
        score = abs(clearance) + footprint
        if best is None or score < best["score"]:
            best = {
                "score": score,
                "ratio": float(ratio),
                "position": [point[0], ground, point[2]],
                "contact": float(max(0.0, 1.0 - score / max(tolerance, 1e-6))),
            }
    return best


def _forward(camera: Camera, direction: np.ndarray) -> float:
    """Cosine between a ray and the view axis, so depth converts to distance."""
    axis = -camera.basis[:, 2]
    return float(np.clip(direction @ axis, 1e-6, 1.0))


def _footprint_error(
    height: np.ndarray,
    size_m: float,
    point: np.ndarray,
    world_m: float,
    ground: float,
) -> float:
    """How uneven the ground is under the object, in metres.

    An object seated at a single sample floats at one corner and buries the
    other on any slope. This is the spread of the terrain under its footprint,
    which the score then trades off against the anchor's own clearance.
    """
    radius = max(world_m * 0.5, 0.05)
    offsets = np.array([[-1, -1], [1, -1], [-1, 1], [1, 1], [0, 0]]) * radius
    samples = [
        sample_height(height, size_m, point[0] + dx, point[2] + dz)
        for dx, dz in offsets
    ]
    return float(max(samples) - min(samples)) * 0.5


def place(
    rows: list[dict],
    camera: Camera,
    height: np.ndarray,
    size_m: float,
    meshes: dict[int, Path] | None = None,
) -> list[dict]:
    """Place every extracted instance. Instances that miss the ground are dropped."""
    meshes = meshes or {}
    placed = []
    for row in rows:
        result = place_instance(
            row, camera, height, size_m, meshes.get(row["index"])
        )
        if result is not None:
            placed.append(result)
    return placed


__all__ = ["place", "place_instance", "glb_bounds", "anchor_pixel"]
