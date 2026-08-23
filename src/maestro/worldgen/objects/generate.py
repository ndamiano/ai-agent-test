"""Run object generation end to end for every selected region.

    objects = generate_objects(scene, terrain, regional, out_dir)

Terrain rendering runs on the engine alone; composition and subject redraws are
jobs on the image queue; finding and describing objects is a queued llm call;
reconstruction is jobs on the mesh queue; placement needs no GPU at all and runs
on the CPU against the height field. Every subject in a region is redrawn as one
fanned-out batch of image jobs rather than one at a time.

Every stage writes its result before the next begins. A run that dies in
reconstruction leaves a composition and its instances on disk, and rerunning
skips what is already there -- which matters, because the composition is two
minutes of image queue. Each subject is redrawn from its own description before
reconstruction: the crop is a card, the redraw is a mesh (measured, see
docs/experiments.md).
"""
from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..backends import ImageModel, MeshModel
from ..planning.models import ScenePlan
from ..terrain.models import TerrainPlan
from .camera import Camera
from .compose import compose, terrain_view
from .extract import extract
from .models import RegionalPlan, RegionalSpec
from .place import place
from . import subject as subject_module


def region_objects(
    spec: RegionalSpec,
    scene: ScenePlan,
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    concept: Path | str | None = None,
) -> list[dict]:
    """Compose, extract, reconstruct and place one region."""
    out_dir = Path(out_dir)
    region = spec.region_id
    height = np.load(out_dir / "heightmap.npy").astype(np.float32)

    terrain_png = out_dir / f"region_{region}_terrain.png"
    camera_path = out_dir / f"camera_{region}.json"
    comp_png = out_dir / f"region_{region}_comp.png"
    instances_path = out_dir / f"instances_{region}.json"

    if terrain_png.exists() and camera_path.exists():
        stored = json.loads(camera_path.read_text())
        camera = Camera.from_dict(stored)
        frame_m = float(stored["frame_m"])
    else:
        terrain_png, camera, frame_m = terrain_view(plan, spec, out_dir, height=height)
        camera_path.write_text(json.dumps({**camera.to_dict(), "frame_m": frame_m}, indent=1))
    print(f"[objects] {region}: {frame_m:.0f} m of ground in frame")

    if instances_path.exists():
        rows = json.loads(instances_path.read_text())
    else:
        if not comp_png.exists():
            compose(
                spec, scene, terrain_png, camera, frame_m, comp_png,
                images=ImageModel(), concept=concept,
            )
            print(f"[objects] {region}: composed {comp_png.name}")
        # the plan's sizes anchor the categories it named; everything else the
        # grounding pass turns up keeps the size the vision model estimated
        sizes = {o.category.lower(): o.typical_size_m for o in spec.objects}
        rows = extract(comp_png, out_dir, sizes=sizes)
        instances_path.write_text(json.dumps(rows, indent=1))
    print(f"[objects] {region}: {len(rows)} instances")
    if not rows:
        return []

    drawn_dir = out_dir / "subjects" / region
    drawn_dir.mkdir(parents=True, exist_ok=True)
    images = ImageModel()

    def _draw_one(row: dict) -> Path:
        drawn = drawn_dir / Path(row["image"]).name
        if not drawn.exists():
            x0, y0, x1, y1 = row["bbox"]
            subject_module.draw(
                row["prompt"], drawn, images=images,
                seed=row["index"] * 17 + 3,
                aspect=(y1 - y0) / max(x1 - x0, 1),
            )
        return drawn

    with ThreadPoolExecutor(max_workers=min(16, len(rows))) as pool:
        futures = {
            pool.submit(contextvars.copy_context().run, _draw_one, row): row for row in rows
        }
        for future, row in futures.items():
            row["drawn"] = str(future.result())
    instances_path.write_text(json.dumps(rows, indent=1))

    for row in rows:
        row["mesh_source"] = row["drawn"]
    produced = MeshModel().reconstruct(
        [row["mesh_source"] for row in rows], out_dir / "objects" / region
    )
    # keyed by the path as given, which is the string that went in -- looking it
    # up as a Path misses every entry and reports a total reconstruction failure
    meshes = {
        row["index"]: produced[row["mesh_source"]]
        for row in rows
        if produced.get(row["mesh_source"]) is not None
    }
    print(f"[objects] {region}: {len(meshes)}/{len(rows)} meshes reconstructed")

    placed = place(rows, camera, height, plan.world.size_m, meshes)
    sources = {row["index"]: row["mesh_source"] for row in rows}
    for item in placed:
        mesh = meshes.get(item["index"])
        item["region_id"] = region
        item["mesh"] = str(mesh) if mesh is not None else None
        item["mesh_source"] = sources[item["index"]]
    placed = [item for item in placed if item["mesh"]]
    dropped = len(rows) - len(placed)
    print(
        f"[objects] {region}: {len(placed)} placed"
        + (f", {dropped} dropped (no mesh or no ground under them)" if dropped else "")
    )
    return placed


def generate_objects(
    scene: ScenePlan,
    plan: TerrainPlan,
    regional: RegionalPlan,
    out_dir: Path | str,
    *,
    concept: Path | str | None = None,
) -> list[dict]:
    """Every selected region's objects, written to `objects.json`.

    The file is what the renderer reads, so writing it is what puts the objects
    in the world.
    """
    out_dir = Path(out_dir)
    everything: list[dict] = []
    for spec in regional.regions:
        everything.extend(
            region_objects(spec, scene, plan, out_dir, concept=concept)
        )
    (out_dir / "objects.json").write_text(json.dumps(everything, indent=1))
    return everything


__all__ = ["generate_objects", "region_objects"]
