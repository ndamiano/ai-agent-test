"""compose_world: what the tool answers, what reaches the game folder, and what runs behind it.

The pipeline itself is stubbed — a world costs an hour of GPU — but everything downstream of it
is real: the same `write_job` a build would call, the real publish, the real thread.
"""
import json
import threading

import numpy as np
import pytest
from PIL import Image

from maestro.codegen.tools import build_tools
from maestro.state import RunState
from maestro.worldgen import compose as world_compose

PLAN = {
    "world": {"size_m": 40.0, "sea_level_m": -1.0, "boundary_blend_m": 2.0,
              "heightmap_resolution": 256},
    "layout": [{"region_id": "meadow", "category": "grassland", "center": [0.5, 0.5],
                "radius": 0.5, "falloff": 0.5, "coverage": 1.0}],
    "terrain": [{"region_id": "meadow", "base_elevation_m": 1.0}],
    "materials": [{"region_id": "meadow", "surface": "grass", "appearance": "green",
                   "variant": "bare brown earth",
                   "scale_m": 2.0}],
    "concept": "A small green meadow under a clear sky.",
}


def _write_world_build(out_dir, *, mesh=None):
    """The files the pipeline's stages leave behind, shaped like the real ones."""
    out_dir.mkdir(parents=True, exist_ok=True)
    height = np.tile(np.linspace(0.0, 4.0, 16, dtype=np.float32), (16, 1))
    np.save(out_dir / "heightmap.npy", height)
    np.save(out_dir / "layout_masks.npy", np.ones((1, 16, 16), np.float32))
    (out_dir / "terrain_plan.json").write_text(json.dumps(PLAN))

    materials = out_dir / "materials"
    materials.mkdir(exist_ok=True)
    for channel, colour in (("albedo", (90, 140, 60)), ("normal", (128, 128, 255))):
        Image.new("RGB", (8, 8), colour).save(materials / f"meadow_{channel}.png")
    (materials / "materials.json").write_text(json.dumps([{
        "region_id": "meadow",
        "albedo": "/gone/meadow_albedo.png",
        "normal": "/gone/meadow_normal.png",
    }]))
    (out_dir / "prototypes.json").write_text(json.dumps([{"category": "rock", "mesh": mesh}]))
    if mesh:
        meshes = out_dir / "subjects" / "meshes"
        meshes.mkdir(parents=True, exist_ok=True)
        (meshes / mesh).write_bytes(b"glTF\x02\x00\x00\x00")
    (out_dir / "scatter.json").write_text(json.dumps({"instances": [{
        "category": "rock", "position": [20.0, 2.0, 20.0],
        "yaw_deg": 0.0, "scale": 1.0, "height_m": 1.0,
    }]}))


@pytest.fixture
def run(tmp_runs):
    rs = RunState("r1")
    (rs.run_dir / "game").mkdir(exist_ok=True)
    return rs


@pytest.fixture
def pipeline(monkeypatch):
    """A `build_world` that writes the stub world and records every leg, with the run scope each
    leg saw. The legs after the first also add the mesh the objects stage would have made."""
    from tools.execution_context import get_run_id

    calls = []
    done = threading.Event()

    def fake_build_world(prompt, out_dir, *, start_at="scene", stop_after="final-render", **kw):
        calls.append({"prompt": prompt, "start_at": start_at, "stop_after": stop_after,
                      "run_id": get_run_id()})
        _write_world_build(out_dir, mesh=None if start_at == "scene" else "rock.glb")
        if stop_after == world_compose.LEGS[-1][1]:
            done.set()
        return {"name": "stub"}

    monkeypatch.setattr("maestro.worldgen.build.build_world", fake_build_world)
    fake_build_world.calls = calls
    fake_build_world.done = done
    return fake_build_world


def _compose(run, pipeline, **kw):
    tools = build_tools(run)
    return tools["compose_world"](description="a meadow by the sea", **kw)


def test_the_tool_answers_with_the_size_and_the_regions(run, pipeline):
    """The model places gameplay by region the turn it hears back, so the answer has to carry the
    metres it places from."""
    res = _compose(run, pipeline)
    assert res["ok"] and res["world"] == "world/world.json"
    assert res["size_m"] == 40.0
    assert res["regions"] == [{"id": "meadow", "category": "grassland",
                               "centre_m": [20.0, 20.0], "radius_m": 20.0}]


def test_the_ground_is_loadable_when_the_tool_returns(run, pipeline):
    """Everything world.json names is beside it, by a path relative to it — the game fetches
    nothing outside its own folder."""
    _compose(run, pipeline)
    world = run.run_dir / "game" / "world"
    job = json.loads((world / "world.json").read_text())
    paths = [job["heightmap"], *job["weight_textures"],
             *(p for r in job["regions"] for p in (r["albedo"], r["normal"]))]
    for relative in paths:
        assert not relative.startswith((".", "/"))
        assert (world / relative).is_file()


def test_the_intermediates_stay_out_of_the_game(run, pipeline):
    _compose(run, pipeline)
    assert (run.run_dir / "world_build" / "heightmap.npy").is_file()
    game = run.run_dir / "game"
    assert not (game / "world" / "heightmap.npy").exists()
    assert not (game / "world_build").exists()


def test_the_first_leg_is_synchronous_and_the_rest_are_not(run, pipeline):
    """The tool waits for the ground and nothing more: the build writes the game while the
    scenery renders."""
    _compose(run, pipeline)
    assert pipeline.calls[0]["stop_after"] == "construct"
    assert pipeline.done.wait(10)
    assert [(c["start_at"], c["stop_after"]) for c in pipeline.calls[1:]] == list(
        world_compose.LEGS)


def test_the_run_scope_reaches_the_thread(run, pipeline):
    """Every job the later legs enqueue is metered to this game, and a thread starts with none of
    the context that started it."""
    _compose(run, pipeline)
    assert pipeline.done.wait(10)
    assert {c["run_id"] for c in pipeline.calls} == {run.run_id}


def test_a_later_leg_republishes_and_restages(run, pipeline, monkeypatch):
    """A mesh that lands after the build finished has to reach the copy people play."""
    from db import store as db_store
    staged = []
    monkeypatch.setattr(db_store, "game", lambda run_id: {"status": "built"})
    monkeypatch.setattr("maestro.codegen.staging.stage_for_play",
                        lambda run_dir, slug: staged.append(slug))
    _compose(run, pipeline)
    assert pipeline.done.wait(10)
    world = run.run_dir / "game" / "world"
    for _ in range(100):
        if len(staged) == len(world_compose.LEGS):
            break
        threading.Event().wait(0.05)
    job = json.loads((world / "world.json").read_text())
    assert job["instances"]["rock"]["mesh"] == "subjects/meshes/rock.glb"
    assert (world / "subjects" / "meshes" / "rock.glb").is_file()
    assert staged == [run.run_id] * len(world_compose.LEGS)


def test_a_failed_leg_leaves_the_published_world_standing(run, pipeline, monkeypatch, caplog):
    def explode(prompt, out_dir, *, start_at="scene", stop_after="final-render", **kw):
        if start_at == "scene":
            return pipeline(prompt, out_dir, start_at=start_at, stop_after=stop_after)
        raise RuntimeError("the mesh server went away")

    monkeypatch.setattr("maestro.worldgen.build.build_world", explode)
    res = _compose(run, pipeline)
    assert res["ok"]
    assert (run.run_dir / "game" / "world" / "world.json").is_file()


def test_world_json_is_replaced_by_rename(run, pipeline, monkeypatch):
    """A game reading the folder mid-publish must never see a world.json naming a file that is not
    there yet, or half of one."""
    renamed = []
    real_replace = world_compose.os.replace
    monkeypatch.setattr(world_compose.os, "replace",
                        lambda src, dst: (renamed.append(str(dst)), real_replace(src, dst))[1])
    _compose(run, pipeline)
    world = run.run_dir / "game" / "world"
    assert renamed[-1] == str(world / "world.json")
    assert not list(world.rglob("*.tmp"))


def test_a_second_world_is_refused(run, pipeline):
    _compose(run, pipeline)
    again = _compose(run, pipeline)
    assert again["ok"] is False and "world/world.json" in again["error"]


def test_a_description_is_required(run, pipeline):
    tools = build_tools(run)
    assert "needs the argument 'description'" in tools["compose_world"]()["error"]
