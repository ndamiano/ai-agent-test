import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from maestro.codegen import asset_store, scene_chain


PLAN = {"terrain": {"base": "grass", "features": ["water_edge:south"]},
        "placeables": [
            {"name": "The Salty Dog Inn", "kind": "building", "size": "medium"},
            {"name": "barrel", "kind": "decoration", "size": "small", "count": 2}],
        "constraints": [["central", "The Salty Dog Inn"]]}

RESOLVED = {"The Salty Dog Inn": {"key": "inn__fantasy", "phrase": "a wooden inn"},
            "barrel": {"key": "barrel__fantasy", "phrase": "a wooden barrel"}}


@pytest.fixture(autouse=True)
def env(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "game" / "assets").mkdir(parents=True)
    store = tmp_path / "store"
    store.mkdir()
    with patch.object(asset_store, "store_dir", lambda: store), \
         patch.object(scene_chain, "_plan", return_value=PLAN), \
         patch.object(asset_store, "resolve_types", return_value=dict(RESOLVED)), \
         patch.object(scene_chain.RunState, "__init__",
                      lambda self, run_id: setattr(self, "run_dir", run_dir) or None), \
         patch.object(scene_chain, "game_dir", lambda rd: run_dir / "game"):
        yield SimpleNamespace(run_dir=run_dir, root=run_dir / "game", store=store)


def _compose(env, **jobs_out):
    calls = []

    def enqueue(queue, payload, **kw):
        calls.append({"queue": queue, "payload": payload, **kw})
    with patch.object(scene_chain.db_store, "enqueue_job", side_effect=enqueue):
        out = scene_chain.compose(env.root, "run-1", "harbor", "town", "fishing town",
                                  7, 32, 24)
    return out, calls


def test_compose_writes_truth_and_placeholder_before_any_art(env):
    out, calls = _compose(env)
    assert out["ok"]
    scene = json.loads((env.root / "assets/harbor_scene.json").read_text())
    assert scene["walkable"] and scene["ground"] == "assets/harbor_ground.png"
    assert scene["cell_px"] == scene_chain.CELL
    img = Image.open(env.root / "assets/harbor_ground.png")
    assert img.size == (32 * scene_chain.CELL, 24 * scene_chain.CELL)


def test_compose_enqueues_subjects_for_misses_and_one_terrain(env):
    out, calls = _compose(env)
    subjects = [c for c in calls if c["metadata"].get("store_key")]
    terrain = [c for c in calls if c["metadata"].get("role") == "terrain"]
    assert {c["metadata"]["store_key"] for c in subjects} == \
           {"inn__fantasy", "barrel__fantasy"}
    assert len(terrain) == 1
    batch_ids = {c["batch_id"] for c in calls}
    assert len(batch_ids) == 1
    assert all(c["game_id"] == "run-1" for c in calls)
    assert all(c["metadata"]["then"]["finalize"] == "scene_objects" for c in calls)


def test_store_hit_skips_subject_job(env):
    asset_store.deposit_subject("inn__fantasy", b"s", "a wooden inn", "m")
    asset_store.deposit_mesh("inn__fantasy", b"g", "t")
    asset_store.deposit_sprite("inn__fantasy", b"p", "cam")
    out, calls = _compose(env)
    keys = {c["metadata"].get("store_key") for c in calls} - {None}
    assert keys == {"barrel__fantasy"}


def test_claimed_type_not_double_rendered(env):
    assert asset_store.claim("inn__fantasy", "other-run")
    out, calls = _compose(env)
    keys = {c["metadata"].get("store_key") for c in calls} - {None}
    assert keys == {"barrel__fantasy"}


def test_budget_refusal_keeps_code_ground(env):
    with patch.object(scene_chain.db_store, "enqueue_job",
                      side_effect=scene_chain.db_store.InsufficientCompute("no", 0, 1)):
        out = scene_chain.compose(env.root, "run-1", "harbor", "town", "s", 7, 32, 24)
    assert out["ok"]
    assert (env.root / "assets/harbor_ground.png").exists()
    assert (env.root / "assets/harbor_scene.json").exists()


def _seed_state(env, w=32, h=24):
    grid = ["0" * w for _ in range(h)]
    state = {"run_id": "run-1", "scene_id": "harbor", "style": "fantasy",
             "ground_rel": "assets/harbor_ground.png",
             "blockout": {"cells": [w, h], "terrain_grid": grid,
                          "terrain_names": ["grass"],
                          "boxes": [{"kind": "building", "name": "The Salty Dog Inn",
                                     "x": 4, "y": 4, "w": 3, "h": 3}]},
             "resolved": RESOLVED}
    p = scene_chain._scene_state_path(env.run_dir, "harbor")
    p.write_text(json.dumps(state))
    Image.new("RGB", (w * scene_chain.CELL, h * scene_chain.CELL), (90, 140, 80)) \
        .save(env.root / "assets/harbor_ground.png")
    return state


def _complete_entry(key, color):
    im = Image.new("RGBA", (64, 64), color)
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    asset_store.deposit_subject(key, buf.getvalue(), "p", "m")
    asset_store.deposit_mesh(key, b"g", "t")
    asset_store.deposit_sprite(key, buf.getvalue(), "cam")


def test_objects_finalize_composites_and_enqueues_embed(env):
    _seed_state(env)
    _complete_entry("inn__fantasy", (200, 40, 40, 255))
    calls = []
    with patch.object(scene_chain.db_store, "enqueue_job",
                      side_effect=lambda q, p, **kw: calls.append((q, p, kw))):
        scene_chain._finalize_scene_objects({"run_id": "run-1", "scene_id": "harbor"}, [])
    composed = scene_chain._scene_state_path(env.run_dir, "harbor") \
        .with_suffix(".composed.png")
    assert composed.exists()
    px = np.asarray(Image.open(composed))
    assert (px[:, :, 0] > 150).any()  # the inn sprite reached the canvas
    assert len(calls) == 1
    q, payload, kw = calls[0]
    assert q == "image" and payload["uploads"]
    assert kw["metadata"]["then"]["finalize"] == "scene_embed"


def test_objects_finalize_budget_refusal_lands_composite(env):
    _seed_state(env)
    _complete_entry("inn__fantasy", (200, 40, 40, 255))
    with patch.object(scene_chain.db_store, "enqueue_job",
                      side_effect=scene_chain.db_store.InsufficientCompute("no", 0, 1)), \
         patch.object(scene_chain.db_store, "game", return_value={"status": "building"}):
        scene_chain._finalize_scene_objects({"run_id": "run-1", "scene_id": "harbor"}, [])
    landed = Image.open(env.root / "assets/harbor_ground.png")
    assert (np.asarray(landed)[:, :, 0] > 150).any()


def test_embed_finalize_lands_final_and_detects_drift(env):
    state = _seed_state(env)
    w, h = 32 * scene_chain.CELL, 24 * scene_chain.CELL
    composed = Image.new("RGB", (w, h), (90, 140, 80))
    composed.save(scene_chain._scene_state_path(env.run_dir, "harbor")
                  .with_suffix(".composed.png"))
    final = composed.copy()
    # blank the inn's box — the edit pass "ate" the object
    px = np.asarray(final).copy()
    c = scene_chain.CELL
    px[4 * c:7 * c, 4 * c:7 * c] = (255, 255, 255)
    Image.fromarray(px).save(scene_chain._scene_state_path(env.run_dir, "harbor")
                             .with_suffix(".final.png"))
    with patch.object(scene_chain.db_store, "game", return_value={"status": "building"}):
        scene_chain._finalize_scene_embed({"run_id": "run-1", "scene_id": "harbor"}, [])
    landed = np.asarray(Image.open(env.root / "assets/harbor_ground.png"))
    assert (landed[4 * c + 5, 4 * c + 5] > 250).all()  # final image landed
    saved = json.loads(scene_chain._scene_state_path(env.run_dir, "harbor").read_text())
    assert saved["drift"] == ["The Salty Dog Inn"]


def test_embed_finalize_without_final_falls_back_to_composite(env):
    _seed_state(env)
    w, h = 32 * scene_chain.CELL, 24 * scene_chain.CELL
    Image.new("RGB", (w, h), (10, 10, 200)).save(
        scene_chain._scene_state_path(env.run_dir, "harbor").with_suffix(".composed.png"))
    with patch.object(scene_chain.db_store, "game", return_value={"status": "building"}):
        scene_chain._finalize_scene_embed({"run_id": "run-1", "scene_id": "harbor"}, [])
    landed = np.asarray(Image.open(env.root / "assets/harbor_ground.png"))
    assert (landed[5, 5] == (10, 10, 200)).all()


def test_refused_subject_releases_claim(env):
    _seed_state(env)
    assert asset_store.claim("inn__fantasy", "run-1") is True
    img_file = env.run_dir / "blob.png"
    img_file.write_bytes(b"x")
    md = {"run_id": "run-1", "scene_id": "harbor", "store_key": "inn__fantasy",
          "then": {"finalize": "scene_objects"}}
    with patch.object(scene_chain, "render_verdict", return_value="explicit"), \
         patch("tools.safety.log_violation"):
        out = scene_chain._scene_mesh_from_image(md, {"images": [{"file": str(img_file)}]})
    assert out is None
    assert asset_store.claim("inn__fantasy", "run-2")  # claim was released


def test_all_hits_and_no_budget_still_composites_from_store(env):
    _complete_entry("inn__fantasy", (200, 40, 40, 255))
    _complete_entry("barrel__fantasy", (40, 40, 200, 255))
    with patch.object(scene_chain.db_store, "enqueue_job",
                      side_effect=scene_chain.db_store.InsufficientCompute("no", 0, 1)):
        out = scene_chain.compose(env.root, "run-1", "harbor", "town", "fishing town",
                                  7, 32, 24)
    assert out["ok"]
    px = np.asarray(Image.open(env.root / "assets/harbor_ground.png"))
    assert (px[:, :, 0] > 150).any()  # a store sprite reached the ground
