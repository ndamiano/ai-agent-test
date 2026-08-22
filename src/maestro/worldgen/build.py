"""One prompt to a finished world — every stage of the paper, in order.

    world = build_world("A small town in the middle of a jungle.", "output/town")

Until now each stage has been run by hand, which is fine while a stage is being
written and useless for answering the only question that matters about a
pipeline: does it work from end to end on a scene it has never seen.

The order is the paper's:

    2.1  intent, then the scene plan                    P
    2.2  terrain plan, concept image                    P_terrain, I_concept
         layout map, asset and material generation      A_terrain
         height field, scattering                       T
         terrain refinement
    2.3  regional planning                              P_regional
         composition, extraction, reconstruction        O_r
         scene refinement, regeneration

Every stage writes into `out_dir` before the next begins, and every stage is
skippable by `start_at`, so a run that fails in reconstruction can be resumed
rather than repeated. Every GPU call inside a stage is a job on the worker-pull
queue, not a call to a locally-held model.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .objects.generate import generate_objects
from .objects.models import RegionalPlan
from .objects.plan import regional_stage
from .objects.refine import apply_regenerations, refine_scene
from .planning.generate_spec import generate_spec
from .planning.models import ScenePlan
from .terrain import construct as construct_module
from .terrain import generate as generate_module
from .terrain import refine as terrain_refine
from .terrain.models import TerrainPlan
from .terrain.plan import terrain_stage
from .terrain.render import render

STAGES = (
    "scene",
    "terrain-plan",
    "terrain-assets",
    "construct",
    "terrain-refine",
    "regional-plan",
    "objects",
    "scene-refine",
    "final-render",
)


class _Clock:
    """Stage timings, printed as they happen and returned at the end."""

    def __init__(self) -> None:
        self.times: dict[str, float] = {}
        self._started = time.time()

    def mark(self, stage: str) -> None:
        now = time.time()
        self.times[stage] = now - self._started
        print(f"[build] {stage} took {self.times[stage]:.0f}s", flush=True)
        self._started = now

    def skip(self, stage: str) -> None:
        print(f"[build] {stage} skipped (already present)", flush=True)
        self._started = time.time()


def _summary(scene: ScenePlan, out_dir: Path, clock: "_Clock", started: float, *,
             developed: list[str] | None = None, objects: int = 0,
             views: list[str] | None = None) -> dict:
    total = time.time() - started
    print(f"[build] finished {scene.name!r} in {total / 60:.1f} min", flush=True)
    return {
        "name": scene.name,
        "out_dir": str(out_dir),
        "regions": [r.id for r in scene.regions],
        "developed": developed or [],
        "objects": objects,
        "views": views or [],
        "timings": clock.times,
        "total_s": total,
    }


def build_world(
    prompt: str,
    out_dir: Path | str,
    *,
    terrain_rounds: int = 2,
    scene_rounds: int = 2,
    start_at: str = "scene",
    stop_after: str = STAGES[-1],
    regenerate: bool = True,
) -> dict:
    """Build a whole world from one sentence. Returns what was made and how long it took.

    `start_at` names the first stage to run and `stop_after` the last; everything
    before the first is loaded from `out_dir` instead, and everything after the
    last is left for a later call to resume. A caller that needs a world on disk
    sooner than the whole pipeline can give one runs the prefix, then the rest.
    The stages are, in order: {}.
    """
    for name in (start_at, stop_after):
        if name not in STAGES:
            raise ValueError(f"unknown stage {name!r}; expected one of {list(STAGES)}")
    begin, end = STAGES.index(start_at), STAGES.index(stop_after)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clock = _Clock()
    started = time.time()

    def running(stage: str) -> bool:
        if STAGES.index(stage) > end:
            return False
        if STAGES.index(stage) < begin:
            clock.skip(stage)
            return False
        print(f"[build] === {stage} ===", flush=True)
        return True

    # -- 2.1 ----------------------------------------------------------------
    scene_path = out_dir / "plan.json"
    if running("scene"):
        scene = generate_spec(prompt)
        scene_path.write_text(scene.model_dump_json(indent=2))
        clock.mark("scene")
    else:
        scene = ScenePlan.model_validate_json(scene_path.read_text())
    print(f"[build] scene {scene.name!r}: {[r.id for r in scene.regions]}", flush=True)

    # -- 2.2.1 --------------------------------------------------------------
    plan_path = out_dir / "terrain_plan.json"
    if running("terrain-plan"):
        plan = terrain_stage(scene, out_dir)
        clock.mark("terrain-plan")
    else:
        plan = TerrainPlan.model_validate_json(plan_path.read_text())

    # -- 2.2.2 --------------------------------------------------------------
    if running("terrain-assets"):
        generate_module.generate_terrain_assets(plan, out_dir)
        clock.mark("terrain-assets")

    if running("construct"):
        summary = construct_module.construct(plan, out_dir)
        elevation = summary.get("elevation_m", {})
        relief = float(elevation.get("max", 0.0)) - float(elevation.get("min", 0.0))
        print(
            f"[build] terrain {relief:.0f} m of relief, "
            f"{sum(summary.get('scatter', {}).values())} scattered",
            flush=True,
        )
        clock.mark("construct")

    # -- 2.2.3 --------------------------------------------------------------
    if running("terrain-refine"):
        plan = terrain_refine.refine(plan, out_dir, rounds=terrain_rounds)
        clock.mark("terrain-refine")
    else:
        plan = TerrainPlan.model_validate_json(plan_path.read_text())

    if end < STAGES.index("regional-plan"):
        return _summary(scene, out_dir, clock, started)

    # the regional planner reads the world; make sure there is one to read
    height = np.load(out_dir / "heightmap.npy").astype(np.float32)
    if not (out_dir / "view_top.png").exists():
        render(plan, out_dir)

    # -- 2.3.1 --------------------------------------------------------------
    regional_path = out_dir / "regional_plan.json"
    if running("regional-plan"):
        regional = regional_stage(scene, plan, out_dir)
        clock.mark("regional-plan")
    else:
        regional = RegionalPlan.model_validate_json(regional_path.read_text())
    print(
        f"[build] developing {[r.region_id for r in regional.regions]}, "
        f"skipping {sorted(regional.skipped)}",
        flush=True,
    )

    if end < STAGES.index("objects"):
        return _summary(scene, out_dir, clock, started,
                        developed=[r.region_id for r in regional.regions])

    # -- 2.3.2 --------------------------------------------------------------
    if running("objects"):
        placed = generate_objects(
            scene, plan, regional, out_dir,
            concept=out_dir / "concept.png",
        )
        clock.mark("objects")
    else:
        placed = json.loads((out_dir / "objects.json").read_text())
    print(f"[build] {len(placed)} objects placed", flush=True)

    # -- 2.3.3 --------------------------------------------------------------
    if running("scene-refine") and placed:
        editor = refine_scene(out_dir, plan, rounds=scene_rounds)
        if regenerate and editor.regenerate:
            apply_regenerations(editor, out_dir)
            # the rebuilt meshes are a different shape, so what they rest on is
            # a different question; one more pass settles them
            editor = refine_scene(out_dir, plan, rounds=1)
        clock.mark("scene-refine")

    if running("final-render"):
        views = render(plan, out_dir)
        clock.mark("final-render")
    else:
        views = []

    return _summary(scene, out_dir, clock, started,
                    developed=[r.region_id for r in regional.regions],
                    objects=len(placed), views=[str(v) for v in views])


build_world.__doc__ = build_world.__doc__.format(", ".join(STAGES))

__all__ = ["build_world", "STAGES"]
