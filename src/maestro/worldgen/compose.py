"""`compose_world`'s build face: a world built into the game folder while the build carries on.

The cut between what the tool waits for and what runs behind it is where the world's own
stage order puts it. `world.json` names the height field, the region materials and the
scattered meshes, so the first moment there is a world to write at all is after `construct`:
the plan says which regions exist, the assets stage draws their materials, and construct
lays the height field and scatters. Everything before that is a world with no ground, and
anything the tool could return earlier would be a promise about regions that have not been
placed yet — and the model places gameplay BY REGION the turn it hears back.

So the tool blocks through `construct` (~15 min of the ~100 the whole world takes, most of
it the region materials) and returns the size and the regions. From there the ground loads:
`world.json` and `heightmap.f32` are on disk, every region has a material, and a world with
no meshes yet still COLLIDES, because the loader reads collision from the stated heights and
never from a GLB.

The rest — terrain refinement, the regional plan, the objects, scene refinement — runs on
one thread for that run, in three legs, and PUBLISHES after each: refinement moves the
ground, objects add meshes and placements, scene refinement rebuilds meshes. A publish
copies only the files `world.json` names into `<game>/world/`, then replaces `world.json`
itself by rename, so a browser reading the folder mid-publish never sees a world pointing at
a file that is not there yet. The build is never blocked by any of it: the thread enqueues
GPU jobs like any other work, and a build that calls `done` while the world is still
rendering finalizes on a game whose ground already loads.

The intermediates stay in `<run_dir>/world_build/` — a quarter of a gigabyte of concept
images, per-region renders and refinement views that no game reads.

A leg that fails is logged and left: the pipeline resumes by stage, so what is published
stands and the world simply stops improving.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

WORLD_DIR = "world"
BUILD_DIR = "world_build"

# Each leg ends at a stage that changes what a game draws, so each is followed by a publish.
LEGS = (("terrain-refine", "terrain-refine"),
        ("regional-plan", "objects"),
        ("scene-refine", "final-render"))


def build_dir(run_dir) -> Path:
    return Path(run_dir) / BUILD_DIR


def world_dir(game_root) -> Path:
    return Path(game_root) / WORLD_DIR


def compose(game_root: Path, run_dir, run_id: str, description: str,
            seed: Optional[int]) -> Dict:
    """Build the world's ground synchronously, publish it, and start the art behind it."""
    from maestro.worldgen.build import build_world
    from tools.execution_context import run_scope

    out_dir = build_dir(run_dir)
    prompt = description if seed is None else f"{description} (variation {seed})"
    # The run scope is what every job this raises is admitted and metered against; a tool call
    # runs outside the build's own.
    with run_scope(run_id):
        build_world(prompt, out_dir, stop_after="construct")
        job = publish(out_dir, game_root)

    threading.Thread(target=_finish, args=(game_root, out_dir, run_id, prompt),
                     daemon=True).start()

    return {"ok": True,
            "world": f"{WORLD_DIR}/world.json",
            "size_m": job["size_m"],
            "regions": [{"id": r["region_id"], "category": r["category"],
                         "centre_m": r["centre_m"], "radius_m": r["radius_m"]}
                        for r in job["regions"]],
            "note": ("the world is written and loads now. Its trees, rocks and buildings are "
                     "still rendering and appear in the same world.json as they land — keep "
                     "loading it from this path and do not build a second world.")}


def _finish(game_root: Path, out_dir: Path, run_id: str, prompt: str) -> None:
    """The legs after the ground, on this run's own thread.

    `run_scope` is re-entered here rather than inherited: a thread starts with none of the
    context that started it, and every job these stages enqueue is metered to this game."""
    from maestro.worldgen.build import build_world
    from tools.execution_context import run_scope

    with run_scope(run_id):
        for start_at, stop_after in LEGS:
            try:
                build_world(prompt, out_dir, start_at=start_at, stop_after=stop_after)
            except Exception:
                logger.exception("world %s: %s..%s failed — the published world stands",
                                 run_id, start_at, stop_after)
                return
            try:
                publish(out_dir, game_root)
                _restage(run_id)
            except Exception:
                logger.exception("world %s: publishing after %s failed", run_id, stop_after)


def _restage(run_id: str) -> None:
    """A world that lands after the build finished must reach the staged copy people play. A
    build still running stages its own game at finalize, and re-staging under it would publish a
    half-written game."""
    from db import store as db_store
    from maestro.codegen.staging import stage_for_play
    from maestro.state import RunState
    if (db_store.game(run_id) or {}).get("status") == "built":
        stage_for_play(RunState(run_id).run_dir, run_id)


def published_files(job: Dict) -> List[str]:
    """The files a game reads, in `world.json`'s own words. Everything else in the build folder —
    concept images, per-region compositions, refinement views, the roughness maps the loader has
    no channel for — is working material."""
    files = [job["heightmap"], *job["weight_textures"]]
    for region in job["regions"]:
        files += [region["albedo"], region["normal"]]
    files += [group["mesh"] for group in job["instances"].values() if group.get("mesh")]
    return list(dict.fromkeys(files))


def publish(out_dir: Path, game_root: Path) -> Dict:
    """Copy the world a game reads into `<game>/world/`, `world.json` last and by rename.

    `world.json` is rewritten from the world as it stands rather than copied as some stage left
    it: a stage moves the ground, adds a mesh or replaces one, and only a fresh job names what is
    actually on disk. Paths inside it are relative to the folder it sits in and the folder's shape
    is kept, so the copy needs no rewriting to resolve against the game."""
    from maestro.worldgen.terrain.models import TerrainPlan
    from maestro.worldgen.terrain.render import write_job

    out_dir, game_root = Path(out_dir), Path(game_root)
    plan = TerrainPlan.model_validate_json(
        (out_dir / "terrain_plan.json").read_text(encoding="utf-8"))
    job = json.loads(write_job(plan, out_dir).read_text(encoding="utf-8"))
    target = world_dir(game_root)
    target.mkdir(parents=True, exist_ok=True)
    for name in published_files(job):
        src = out_dir / name
        if src.is_file():
            _replace(src, target / name)
    _replace_bytes(target / "world.json", (out_dir / "world.json").read_bytes())
    return job


def _replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _replace_bytes(dst: Path, data: bytes) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dst)
