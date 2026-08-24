import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from maestro.codegen import asset_store, scene_chain


def _layout(w=32, h=24):
    terrain = [{"symbol": "G", "name": "grass", "walkable": True, "color": "#4caf50"}]
    items = [{"symbol": "i", "name": "The Salty Dog Inn", "count": 1, "on_terrain": "G",
              "walkable": False, "cells": 3, "kind": "object"},
             {"symbol": "b", "name": "barrel", "count": 1, "on_terrain": "G",
              "walkable": False, "cells": 1, "kind": "object"}]
    placements = [{"symbol": "i", "row": 4, "col": 4}, {"symbol": "b", "row": 10, "col": 10}]
    from scenegen.layout import walkable_grid, walkable_stats
    grid = ["G" * w for _ in range(h)]
    walk = walkable_grid(grid, terrain, items, placements)
    return {"place": "a town: fishing town", "terrain": terrain, "items": items,
            "grid": grid, "placements": placements, "snapped": 0, "walkable": walk,
            "checks": walkable_stats(walk)}


SPEC = {"grass": {"color": "#6da24c", "phrase": "soft green meadow grass"}}

RESOLVED = {"The Salty Dog Inn": {"key": "inn__fantasy", "phrase": "a wooden inn"},
            "barrel": {"key": "barrel__fantasy", "phrase": "a wooden barrel"}}


@pytest.fixture(autouse=True)
def env(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "game" / "assets").mkdir(parents=True)
    store = tmp_path / "store"
    store.mkdir()
    with patch.object(asset_store, "store_dir", lambda: store), \
         patch.object(scene_chain, "generate_layout",
                      lambda llm, place, w, h: _layout(w, h)), \
         patch.object(scene_chain, "paint_spec", lambda llm, place, terrain: dict(SPEC)), \
         patch.object(scene_chain, "_llm", lambda run_id: None), \
         patch.object(asset_store, "resolve_types", return_value=dict(RESOLVED)), \
         patch.object(scene_chain.RunState, "__init__",
                      lambda self, run_id: setattr(self, "run_dir", run_dir) or None), \
         patch.object(scene_chain, "game_dir", lambda rd: run_dir / "game"):
        yield SimpleNamespace(run_dir=run_dir, root=run_dir / "game", store=store)


def _compose(env):
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
    assert {p["name"] for p in scene["pois"]} == {"The Salty Dog Inn", "barrel"}
    img = Image.open(env.root / "assets/harbor_ground.png")
    assert img.size == (32 * scene_chain.CELL, 24 * scene_chain.CELL)


def test_walkable_stamps_item_footprints(env):
    out, _ = _compose(env)
    scene = json.loads((env.root / "assets/harbor_scene.json").read_text())
    walk = scene["walkable"]
    assert walk[4][4] == "0" and walk[6][6] == "0"
    assert walk[10][10] == "0"
    assert walk[0][0] == "1"


def test_compose_enqueues_subjects_for_misses_and_one_ground(env):
    out, calls = _compose(env)
    subjects = [c for c in calls if c["metadata"].get("store_key")]
    ground = [c for c in calls
              if c["metadata"].get("then", {}).get("enqueue") == "scene_blend_from_regional"]
    assert {c["metadata"]["store_key"] for c in subjects} == \
           {"inn__fantasy", "barrel__fantasy"}
    assert len(ground) == 1
    assert ground[0]["payload"]["uploads"]
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


def test_budget_refusal_keeps_guide_ground(env):
    with patch.object(scene_chain.db_store, "enqueue_job",
                      side_effect=scene_chain.db_store.InsufficientCompute("no", 0, 1)):
        out = scene_chain.compose(env.root, "run-1", "harbor", "town", "s", 7, 32, 24)
    assert out["ok"]
    assert (env.root / "assets/harbor_ground.png").exists()
    assert (env.root / "assets/harbor_scene.json").exists()


def test_paintspec_failure_falls_back_to_tileset_colors(env):
    def boom(llm, place, terrain):
        raise RuntimeError("refused")
    with patch.object(scene_chain, "paint_spec", boom), \
         patch.object(scene_chain.db_store, "enqueue_job",
                      side_effect=scene_chain.db_store.InsufficientCompute("no", 0, 1)):
        out = scene_chain.compose(env.root, "run-1", "harbor", "town", "s", 7, 32, 24)
    assert out["ok"]
    assert (env.root / "assets/harbor_ground.png").exists()


def _seed_state(env, w=32, h=24):
    layout = _layout(w, h)
    state = {"run_id": "run-1", "scene_id": "harbor", "style": "fantasy", "seed": 7,
             "ground_rel": "assets/harbor_ground.png", "terrain": layout["terrain"],
             "grid": layout["grid"], "items": layout["items"],
             "placements": layout["placements"], "spec": SPEC, "resolved": RESOLVED}
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


def test_regional_result_chains_blend_in_same_batch(env):
    _seed_state(env)
    img = Image.new("RGB", (64, 64), (5, 5, 5))
    src = env.run_dir / "regional.png"
    img.save(src)
    md = {"run_id": "run-1", "scene_id": "harbor",
          "then": {"enqueue": "scene_blend_from_regional", "finalize": "scene_objects"}}
    with patch.object(scene_chain, "render_verdict", return_value=None):
        out = scene_chain._scene_blend_from_regional(md, {"images": [{"file": str(src)}]})
    assert out["queue"] == "image"
    assert out["payload"]["uploads"]
    assert out["metadata"]["then"] == {"operations": ["scene_terrain"],
                                      "finalize": "scene_objects"}
    assert not src.exists()


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
    _seed_state(env)
    w, h = 32 * scene_chain.CELL, 24 * scene_chain.CELL
    composed = Image.new("RGB", (w, h), (90, 140, 80))
    composed.save(scene_chain._scene_state_path(env.run_dir, "harbor")
                  .with_suffix(".composed.png"))
    final = composed.copy()
    # blank the inn's footprint — the edit pass "ate" the object
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
