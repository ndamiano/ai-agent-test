"""Status reports for the scene refinement agent.

The agent checks pose, mesh quality and scale, then object-terrain contact --
floating, excessive penetration, unstable support. A render alone cannot answer
any of those: half a metre of penetration and perfect seating look identical
from most angles, and the agent would be guessing.

So every check here is a measurement against the height field and the mesh, and
the agent is given numbers next to the pictures. Each returns a defect or
nothing, phrased as what is wrong rather than as a score, because a report that
says "support 0.31" tells an agent less than one that says three of five
footprint samples are in the air.

Nothing here decides anything. It measures, names the defect, and leaves the
decision -- reseat, rescale, reorient, regenerate, or accept -- to the agent.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .camera import sample_height
from .place import glb_bounds

# A mesh whose shortest axis is this thin relative to its longest is a billboard
# rather than an object. TRELLIS2 returns one whenever the crop it was given is
# too small, too dark, or too occluded to carry geometry, and the result renders
# as a photograph pasted on a slab.
SLAB_RATIO = 0.06

# Contact tolerances, as a fraction of the object's own height. An object is
# allowed to sit a little into the ground -- debris half-buried in gravel is
# correct -- but not to hover.
FLOAT_TOLERANCE = 0.05
SINK_TOLERANCE = 0.35
SUPPORT_MIN = 0.6  # of footprint samples in contact

# How far the measured size may sit from what the planner asked for before it is
# worth reporting. Wide, because the image is the authority and the planner's
# figure is approximate -- this catches the order-of-magnitude case.
SIZE_RATIO = 2.0

FOOTPRINT_SAMPLES = 5


def footprint(
    height: np.ndarray,
    size_m: float,
    position: list[float],
    extent_m: float,
) -> list[float]:
    """Clearance between the object's base and the ground, around its footprint.

    Positive is air under that corner, negative is ground through it.
    """
    radius = max(extent_m * 0.5, 0.05)
    offsets = [(-1, -1), (1, -1), (-1, 1), (1, 1), (0, 0)]
    return [
        float(
            position[1]
            - sample_height(
                height, size_m, position[0] + dx * radius, position[2] + dz * radius
            )
        )
        for dx, dz in offsets
    ]


def inspect(
    item: dict,
    height: np.ndarray,
    size_m: float,
) -> dict:
    """Everything measurable about one placed object."""
    report: dict = {
        "index": item["index"],
        "category": item["category"],
        "position": [round(v, 2) for v in item["position"]],
        "size_m": round(float(item["size_m"]), 2),
        "height_m": round(float(item["height_m"]), 2),
        "stated_size_m": float(item["stated_m"]),
        "yaw_deg": round(float(item["yaw_deg"]), 1),
        "defects": [],
    }

    mesh = item.get("mesh")
    if mesh and Path(mesh).exists():
        low, high = glb_bounds(mesh)
        extent = np.asarray(high) - np.asarray(low)
        longest = float(extent.max()) or 1.0
        thinnest = float(extent.min())
        report["mesh_extent"] = [round(float(v), 3) for v in extent]
        report["flatness"] = round(thinnest / longest, 4)
        if thinnest / longest < SLAB_RATIO:
            report["defects"].append(
                f"mesh is a flat slab ({thinnest / longest:.3f} thickness to length): "
                f"reconstruction produced a billboard, not geometry"
            )
    else:
        report["defects"].append("no mesh")
        return report

    clearances = footprint(height, size_m, item["position"], float(item["size_m"]))
    report["clearance_m"] = [round(c, 2) for c in clearances]
    object_height = max(float(item["height_m"]), 0.05)
    float_limit = FLOAT_TOLERANCE * object_height
    sink_limit = SINK_TOLERANCE * object_height
    in_contact = [c for c in clearances if -sink_limit <= c <= float_limit]
    support = len(in_contact) / len(clearances)
    report["support"] = round(support, 2)

    highest = max(clearances)
    lowest = min(clearances)
    if lowest < -sink_limit:
        report["defects"].append(
            f"buried: ground stands {-lowest:.2f} m through the object, which is "
            f"{object_height:.2f} m tall"
        )
    if highest > float_limit:
        report["defects"].append(
            f"floating: {highest:.2f} m of air under it at one corner"
        )
    if support < SUPPORT_MIN:
        report["defects"].append(
            f"unstable support: {len(in_contact)} of {len(clearances)} footprint "
            f"samples touch the ground"
        )

    ratio = float(item["size_m"]) / max(float(item["stated_m"]), 1e-3)
    if ratio > SIZE_RATIO or ratio < 1.0 / SIZE_RATIO:
        report["defects"].append(
            f"scale: measured {item['size_m']:.1f} m against a stated "
            f"{item['stated_m']:.1f} m for a {item['category']}"
        )
    return report


def survey(
    objects: list[dict],
    height: np.ndarray,
    size_m: float,
) -> list[dict]:
    """Inspect every placed object. Worst first, so the queue is ordered by need."""
    reports = [inspect(item, height, size_m) for item in objects]
    return sorted(reports, key=lambda r: len(r["defects"]), reverse=True)


def summarise(reports: list[dict]) -> str:
    """The survey as the text the agent reads alongside its renders."""
    if not reports:
        return "No objects placed in this region."
    lines = []
    clean = 0
    for report in reports:
        if not report["defects"]:
            clean += 1
            continue
        lines.append(
            f"[{report['index']}] {report['category']} at "
            f"{report['position']}, {report['size_m']:g} m across, "
            f"{report['height_m']:g} m tall, yaw {report['yaw_deg']:g}"
        )
        lines += [f"    - {defect}" for defect in report["defects"]]
    header = (
        f"{len(reports)} objects placed; {clean} pass every check, "
        f"{len(reports) - clean} have defects."
    )
    return "\n".join([header, ""] + lines) if lines else header


__all__ = ["survey", "inspect", "summarise", "footprint"]
