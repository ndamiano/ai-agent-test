"""Assets stage (reskin) — the code path that needs no LLM/ComfyUI: plan parsing, draw-file
detection, the draw rewrite extraction, manifest emission, soft-fail when the image server is down,
and staging carrying the skin across."""

import json
import shutil
import struct

import pytest
from PIL import Image

import tools.comfyui_tools as ct
from db import store
from maestro.codegen import reskin
from maestro.codegen.gates import RUNTIME_DIR, game_dir, stage_for_play


def _write_game(tmp_path):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "main.ts").write_text(
        "export function createGame(kit: Kit): GameObject {\n"
        "  return { config: {}, state: { p: { x: 1, y: 1 } },\n"
        "    update(dt, input, kit) {}, draw(g, kit) { g.circle(1, 1, 8, '#0ff'); } };\n"
        "}\n", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [{"name": "main.ts"}]}), encoding="utf-8")
    return tmp_path


def test_draws_detects_draw_calls():
    assert reskin._draws("g.circle(x,y,8,'#0ff')")
    assert reskin._draws("foo.rect(0,0,4,4,'#f00')")
    assert not reskin._draws("state.enemies.push(e); score += 1")


def test_plan_assets_parses_dedups_and_defaults():
    reply = """```json
    {"sprites":[
      {"id":"Player","prompt":"cyan ship","w":24,"h":24},
      {"id":"player","prompt":"dup dropped"},
      {"id":"enemy","prompt":"red alien"},
      {"id":"bad"}
    ]}```"""
    out = reskin.plan_assets(lambda s, u, m: reply, {"design": {}}, {"main.ts": "x"})
    ids = [s["id"] for s in out]
    assert ids == ["player", "enemy"]          # lowercased, dup + prompt-less dropped
    assert out[0]["w"] == 24
    assert out[1]["w"] == 32 and out[1]["h"] == 32   # defaults


def test_plan_assets_names_the_empty_completion():
    """A thinking model that spends its whole budget reasoning returns no content; the failure must
    name that, not surface three frames away as a JSONDecodeError on column 1."""
    with pytest.raises(ValueError, match="no content"):
        reskin.plan_assets(lambda s, u, m: "", {"design": {}}, {"main.ts": "x"})


def test_reskin_file_extracts_ts_block():
    reply = "here you go:\n```ts\nexport const x = 1;\n```\ntrailing"
    assert reskin.reskin_file(lambda s, u, m: reply, "main.ts", "old", ["player"]).strip() \
        == "export const x = 1;"


def test_write_manifest_shape(tmp_path):
    _write_game(tmp_path)
    reskin.write_manifest(tmp_path, [{"id": "player", "prompt": "p", "w": 24, "h": 24},
                                     {"id": "enemy", "prompt": "e", "w": 16, "h": 16}])
    m = json.loads((game_dir(tmp_path) / "assets.json").read_text())
    assert m["sprites"][0] == {"id": "player", "file": "assets/player.png",
                               "w": 24, "h": 24, "prompt": "p"}
    assert m["sprites"][1]["file"] == "assets/enemy.png"


def test_start_asset_chain_enqueues_one_job_per_item(tmp_path, monkeypatch):
    """Every job lands at once — that depth is what the scaler reads and what keeps a worker from
    idling out with the next asset seconds away."""
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(reskin, "build_item_payload", lambda d: {"kind": "comfy_image"})
    store.create_game("rid", "u1")
    store.charge_game("rid", 1, 10_000.0)

    batch_id = reskin.start_asset_chain(
        "rid", [{"id": "player", "prompt": "p"}, {"id": "enemy", "prompt": "e"}], "3d", True)

    jobs = store.batch_jobs(batch_id)
    assert [j["status"] for j in jobs] == ["pending", "pending"]
    assert [j["metadata"]["asset_id"] for j in jobs] == ["player", "enemy"]
    # 3D fans out image → mesh, so the head job carries the TRELLIS job that follows it.
    assert jobs[0]["metadata"]["then"]["enqueue"] == "mesh_from_image"


def test_start_asset_chain_returns_none_when_every_prompt_is_blocked(tmp_path, monkeypatch):
    """A blocked prompt is never sent. With none left there is no batch, so the caller has to
    finalize directly — no completion ever will."""
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(reskin, "build_item_payload", lambda d: None)
    store.create_game("rid", "u1")
    store.charge_game("rid", 1, 10_000.0)
    assert reskin.start_asset_chain("rid", [{"id": "player", "prompt": "x"}], "2d", True) is None


def test_autocrop_tightens_to_opaque_subject(tmp_path):
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))          # fully transparent
    for x in range(40, 60):
        for y in range(40, 60):
            im.putpixel((x, y), (255, 0, 0, 255))             # 20x20 opaque subject in the middle
    p = tmp_path / "s.png"
    im.save(p)
    reskin._autocrop(p, pad_frac=0.0)
    assert Image.open(p).size == (20, 20)                     # cropped to the subject, margins gone


def test_autocrop_noop_on_fully_transparent(tmp_path):
    p = tmp_path / "blank.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(p)
    reskin._autocrop(p)                                       # no bbox → left as is, no crash
    assert Image.open(p).size == (64, 64)


def test_stage_copies_skin_when_present(tmp_path):
    _write_game(tmp_path)
    reskin.write_manifest(tmp_path, [{"id": "player", "prompt": "p", "w": 24, "h": 24}])
    assets = game_dir(tmp_path) / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "player.png").write_bytes(b"\x89PNG")
    slug = "test_skin_stage"
    try:
        stage_for_play(tmp_path, slug)
        dst = RUNTIME_DIR / "games" / slug
        assert (dst / "assets.json").exists()
        assert (dst / "assets" / "player.png").read_bytes() == b"\x89PNG"
    finally:
        shutil.rmtree(RUNTIME_DIR / "games" / slug, ignore_errors=True)


# ── 3D mesh path ──────────────────────────────────────────────────────────────

def _write_3d_game(tmp_path):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "main.ts").write_text(
        "export function createGame(kit: Kit): GameObject {\n"
        "  return { config: { mode: \"3d\", width: 1280, height: 720 },\n"
        "    state: { world: [] as any[] },\n"
        "    init(kit) { this.state.world.push({ shape: \"box\", x:0,y:1,z:0, w:1,h:2,d:1, color:'#fff' }); },\n"
        "    update(dt, input, kit) {}, draw(g, kit) {} };\n"
        "}\n", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [{"name": "main.ts"}]}), encoding="utf-8")
    return tmp_path


def test_is_3d_detects_mode():
    assert reskin._is_3d({"main.ts": 'config: { mode: "3d", width: 1280 }'})
    assert not reskin._is_3d({"main.ts": 'config: { width: 960, height: 540 }'})


def test_tags_shapes_detects_shape_tag():
    assert reskin._tags_shapes('{ shape: "box", x:0, w:1 }')
    assert reskin._tags_shapes("{ shape: 'sphere', r: 2 }")
    assert not reskin._tags_shapes('{ shape: "ground", size: 200 }')   # ground stays a plane
    assert not reskin._tags_shapes("state.score += 1")


def test_plan_meshes_parses_dedups_and_defaults():
    reply = """```json
    {"meshes":[
      {"id":"Player","prompt":"a robot","w":1,"h":2,"d":1},
      {"id":"player","prompt":"dup dropped"},
      {"id":"crystal","prompt":"a gem"},
      {"id":"nope"}
    ]}```"""
    out = reskin.plan_meshes(lambda s, u, m: reply, {"design": {}}, {"main.ts": "x"})
    assert [m["id"] for m in out] == ["player", "crystal"]     # lowercased, dup + prompt-less dropped
    assert out[0]["h"] == 2
    assert out[1]["w"] == 1 and out[1]["h"] == 1 and out[1]["d"] == 1   # defaults


def test_plannable_src_drops_generated_bodies():
    # WHY: a `// GENERATED` file (worldgen's ~100KB world.ts) is never rewritten and its ids come from
    # its mesh tags, not its body — feeding the body blows the context window. It must be excluded.
    files = {"main.ts": "spawn player", "world.ts": "// GENERATED\n" + "x=1\n" * 5000}
    src = reskin._plannable_src(files)
    assert "spawn player" in src
    assert "x=1" not in src                       # the generated whale is gone


def test_plan_meshes_excludes_generated_body_but_keeps_its_ids():
    # WHY: dropping world.ts's body must NOT drop its objects — the tagged ids still reach the plan as
    # REQUIRED ids (scanned from the full file set) and the safety net emits a mesh for each.
    captured = {}

    def fake_infer(system, user, mt):
        captured["user"] = user
        return '```json\n{"meshes":[{"id":"player","prompt":"a robot","w":1,"h":2,"d":1}]}```'

    files = {"main.ts": "spawn player entity",
             "world.ts": '// GENERATED\nconst b = { shape:"box", mesh:"storehouse" };\n' + "n=0\n" * 5000}
    out = reskin.plan_meshes(fake_infer, {"design": {}}, files)
    assert "n=0" not in captured["user"]                    # generated body excluded from the prompt
    assert "storehouse" in captured["user"]                 # but its required id is named
    assert "storehouse" in [m["id"] for m in out]           # and gets a mesh (safety net)
    assert "player" in [m["id"] for m in out]


def test_reskin_mesh_file_extracts_ts_block():
    reply = "sure:\n```ts\nexport const y = 2;\n```\ndone"
    assert reskin.reskin_mesh_file(lambda s, u, m: reply, "main.ts", "old", ["player"]).strip() \
        == "export const y = 2;"


def test_write_mesh_manifest_shape(tmp_path):
    _write_3d_game(tmp_path)
    reskin.write_mesh_manifest(tmp_path, [{"id": "player", "prompt": "p", "w": 1, "h": 2, "d": 1},
                                          {"id": "crystal", "prompt": "c", "w": 1, "h": 1, "d": 1}])
    m = json.loads((game_dir(tmp_path) / "assets.json").read_text())
    assert m["meshes"][0] == {"id": "player", "file": "assets/player.glb", "prompt": "p"}
    assert m["meshes"][1]["file"] == "assets/crystal.glb"




def test_stage_copies_mesh_skin_when_present(tmp_path):
    _write_3d_game(tmp_path)
    reskin.write_mesh_manifest(tmp_path, [{"id": "player", "prompt": "p", "w": 1, "h": 2, "d": 1}])
    assets = game_dir(tmp_path) / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "player.glb").write_bytes(b"glTF")
    slug = "test_mesh_stage"
    try:
        stage_for_play(tmp_path, slug)
        dst = RUNTIME_DIR / "games" / slug
        assert json.loads((dst / "assets.json").read_text())["meshes"][0]["file"] == "assets/player.glb"
        assert (dst / "assets" / "player.glb").read_bytes() == b"glTF"
    finally:
        shutil.rmtree(RUNTIME_DIR / "games" / slug, ignore_errors=True)


def test_decimate_glb_end_to_end(tmp_path):
    """The decimation hook must run the real node script on a real (tiny) GLB and leave a valid,
    no-larger file — and keep the original untouched when the script fails."""

    def tri_glb() -> bytes:
        buf = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0) + struct.pack("<3I", 0, 1, 2)
        doc = json.dumps({
            "asset": {"version": "2.0"},
            "scenes": [{"nodes": [0]}], "scene": 0, "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                 "min": [0, 0, 0], "max": [1, 1, 0]},
                {"bufferView": 1, "componentType": 5125, "count": 3, "type": "SCALAR"},
            ],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36},
                            {"buffer": 0, "byteOffset": 36, "byteLength": 12}],
            "buffers": [{"byteLength": len(buf)}],
        }).encode()
        doc += b" " * (-len(doc) % 4)
        total = 12 + 8 + len(doc) + 8 + len(buf)
        return (b"glTF" + struct.pack("<II", 2, total)
                + struct.pack("<I", len(doc)) + b"JSON" + doc
                + struct.pack("<I", len(buf)) + b"BIN\x00" + buf)

    glb = tmp_path / "thing.glb"
    glb.write_bytes(tri_glb())
    orig_size = glb.stat().st_size
    assert ct._decimate_glb(str(glb)) is True
    out = glb.read_bytes()
    assert out[:4] == b"glTF" and len(out) <= orig_size * 2   # valid, not exploded

    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb")
    assert ct._decimate_glb(str(bad)) is False
    assert bad.read_bytes() == b"not a glb"   # original untouched on failure


def test_rewrite_budget_scales_with_the_file():
    """A flat cap truncates a big file mid-token; the budget must track the input size."""
    assert reskin._rewrite_budget("x" * 1000) == 6000        # floor for small files
    assert reskin._rewrite_budget("x" * 24000) == 12000      # a 622-line main.ts needs > 6000


def test_truncated_rewrite_is_left_unwritten(tmp_path, monkeypatch):
    """A cut-off completion must never overwrite a working file — that is what turns an additive
    skin into a syntax error the gate loop then burns its whole step budget repairing."""
    src = "function a() {\n" + "  const x = 1;\n" * 40 + "}\n"
    assert reskin._looks_truncated(src, src[:len(src) // 3])   # cut off mid-body
    assert reskin._looks_truncated(src, src + "\nfunction b() {")  # unbalanced braces
    assert not reskin._looks_truncated(src, src.replace("const x", "const y"))


# ── the early + green asset lanes ─────────────────────────────────────────────
def _write_data(tmp_path, rows):
    dd = game_dir(tmp_path) / "data"
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "manifest.json").write_text(json.dumps(
        {"datasets": [{"name": "units", "fields": {}}]}), encoding="utf-8")
    (dd / "units.json").write_text(json.dumps(rows), encoding="utf-8")


@pytest.fixture
def lane_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(reskin, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(reskin, "build_item_payload", lambda d: {"kind": "comfy_image"})
    monkeypatch.setattr(reskin, "RunState",
                        lambda rid: type("S", (), {"run_dir": tmp_path})())
    store.create_game("rid", "u1")
    store.charge_game("rid", 1, 10_000.0)
    _write_game(tmp_path)
    return tmp_path


def test_early_lane_waits_for_a_plan(lane_env):
    """No data yet → None, so the build retries next sweep instead of settling."""
    assert reskin.start_assets_early("rid", lane_env, {"mode": "2d"}) is None


def test_early_lane_enqueues_from_data_and_writes_the_manifest(lane_env):
    _write_data(lane_env, [{"id": "goblin", "look": "a green goblin"},
                           {"id": "plain", "note": "no look"}])
    batch = reskin.start_assets_early("rid", lane_env, {"mode": "2d"})
    assert batch
    jobs = store.batch_jobs(batch)
    assert [j["metadata"]["asset_id"] for j in jobs] == ["goblin"]
    assert jobs[0]["metadata"]["gate_ok"] is False       # staging deferred to the build
    m = json.loads((game_dir(lane_env) / "assets.json").read_text())
    assert m["sprites"][0]["id"] == "goblin"


def test_early_lane_settles_when_assets_exist_or_batch_live(lane_env):
    _write_data(lane_env, [{"id": "goblin", "look": "a green goblin"}])
    batch = reskin.start_assets_early("rid", lane_env, {"mode": "2d"})
    assert batch
    # assets.json now exists AND the batch is live — either alone settles the lane
    assert reskin.start_assets_early("rid", lane_env, {"mode": "2d"}) == ""


def test_green_lane_runs_the_full_skin_when_no_early_batch(lane_env, monkeypatch):
    called = []
    monkeypatch.setattr(reskin, "add_assets", lambda rid, **kw: called.append(rid))
    reskin.auto_skin("rid")
    assert called == ["rid"]


def test_green_lane_noops_when_already_skinned(lane_env, monkeypatch):
    (game_dir(lane_env) / "assets.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(reskin, "add_assets",
                        lambda rid, **kw: pytest.fail("skinned game must not re-skin"))
    reskin.auto_skin("rid")


def test_green_lane_skips_the_rewrite_when_data_bound(lane_env, monkeypatch):
    """A game that spawns the things the plan depicts binds them through kit.spawn itself — the
    green lane must spend zero LLM calls on it."""
    _write_data(lane_env, [{"id": "goblin", "look": "a green goblin"}])
    (game_dir(lane_env) / "game.ts").write_text(
        'kit.spawn(state.world, { type: "goblin", x: 1, y: 2 });\n', encoding="utf-8")
    monkeypatch.setattr(reskin, "_reskin_and_gate",
                        lambda *a, **k: pytest.fail("bound game must not be rewritten"))
    reskin.auto_skin("rid", early_batch="b1")


def test_a_data_bound_skin_never_runs_a_build(lane_env, monkeypatch):
    """Nothing is rewritten on this path, so there is nothing for a build to fix. run_build here
    re-ran the whole chain — spec audit included — to answer a question the gates answer alone:
    measured, two asset runs on one game each spent a 7-claim audit to enqueue 8 images that needed
    no model call at all."""
    from types import SimpleNamespace

    monkeypatch.setattr(reskin, "run_build",
                        lambda *a, **k: pytest.fail("a stage that rewrote nothing must not build"))
    swept = []

    def fake_collect(module, ctx):
        swept.append(ctx)
        return []

    monkeypatch.setattr("maestro.codegen.build_chain.collect_errors", fake_collect)
    monkeypatch.setattr("maestro.modules.context.build_context", lambda spec, state: "ctx")
    state = SimpleNamespace(run_dir=lane_env, read_spec=lambda: {"design": {}})

    result = reskin._regate("rid", state, 40)

    assert result.ok is True and result.steps == 0
    assert swept == ["ctx"]                       # the gates were swept, not re-built


def test_a_data_bound_skin_reports_a_game_that_was_never_green(lane_env, monkeypatch):
    from types import SimpleNamespace

    from maestro.modules.module import Error, ErrorType

    err = Error(type=ErrorType.FIX, code="typechecks", component="game", message="boom")
    monkeypatch.setattr("maestro.codegen.build_chain.collect_errors",
                        lambda module, ctx: [(None, err)])
    monkeypatch.setattr("maestro.modules.context.build_context", lambda spec, state: "ctx")
    state = SimpleNamespace(run_dir=lane_env, read_spec=lambda: {"design": {}})

    result = reskin._regate("rid", state, 40)

    assert result.ok is False and result.failures == [err]


def test_green_lane_defers_to_an_inflight_skin(lane_env, monkeypatch):
    monkeypatch.setattr(reskin, "add_assets",
                        lambda rid, **kw: pytest.fail("in-flight skin owns the endgame"))
    reskin._skinning.add("rid")
    try:
        reskin.auto_skin("rid")
    finally:
        reskin._skinning.discard("rid")


def test_add_assets_is_exclusive(lane_env, monkeypatch):
    with reskin._exclusive("rid"):
        with pytest.raises(reskin.AlreadySkinning):
            with reskin._exclusive("rid"):
                pass
    # released on exit
    with reskin._exclusive("rid"):
        pass


# ── regenerate: asset context + img2img ───────────────────────────────────────
def test_merge_regen_prompt_folds_the_note_into_the_original():
    infer = lambda s, u, m: "a green goblin sprite with a red cape"
    assert reskin._merge_regen_prompt(infer, "a green goblin sprite", "give him a red cape") \
        == "a green goblin sprite with a red cape"


def test_merge_regen_prompt_without_context_is_the_note_verbatim():
    def boom(*a):
        raise AssertionError("no original context — no llm call")
    assert reskin._merge_regen_prompt(boom, None, "a red dragon") == "a red dragon"


def test_merge_regen_prompt_fails_open_on_infer_error():
    def boom(*a):
        raise RuntimeError("llm queue down")
    assert reskin._merge_regen_prompt(boom, "a goblin", "make it red") == "make it red"


def test_asset_context_reads_the_manifest_prompt(tmp_path):
    _write_game(tmp_path)
    reskin.write_manifest(tmp_path, [{"id": "goblin", "prompt": "a green goblin", "w": 24, "h": 24}])
    assert reskin._asset_context(tmp_path, "goblin", "2d") == "a green goblin"
    assert reskin._asset_context(tmp_path, "nope", "2d") is None


def test_asset_context_falls_back_to_data_look(tmp_path):
    """Games skinned before manifests persisted prompts still have the rows' look."""
    _write_game(tmp_path)
    _write_data(tmp_path, [{"id": "goblin", "look": "a green goblin"}])
    assert reskin._asset_context(tmp_path, "goblin", "2d") == "a green goblin"


@pytest.fixture
def regen_env(lane_env, monkeypatch):
    reskin.write_manifest(lane_env, [{"id": "goblin", "prompt": "a green goblin", "w": 24, "h": 24}])
    monkeypatch.setattr(reskin, "_make_infer",
                        lambda: (lambda s, u, m: "a green goblin with a red cape"))
    captured = {}

    def fake_payload(desc, init_image_b64=None):
        captured["prompt"] = desc
        captured["init"] = init_image_b64
        return {"kind": "comfy_image"}

    monkeypatch.setattr(reskin, "build_item_payload", fake_payload)
    return lane_env, captured


def test_regenerate_merges_the_asset_context(regen_env):
    env, captured = regen_env
    batch = reskin.regenerate_asset("rid", "goblin", "give him a red cape")
    assert batch
    assert captured["prompt"] == "a green goblin with a red cape"
    assert captured["init"] is None
    (job,) = store.batch_jobs(batch)
    assert job["metadata"]["asset_id"] == "goblin" and job["metadata"]["gate_ok"] is True


def test_regenerate_img2img_seeds_from_the_current_sprite(regen_env):
    env, captured = regen_env
    assets = game_dir(env) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "goblin.png").write_bytes(b"png-bytes")
    assert reskin.regenerate_asset("rid", "goblin", "redder", mode="img2img")
    import base64 as b64mod
    assert captured["init"] == b64mod.b64encode(b"png-bytes").decode("ascii")


def test_regenerate_img2img_without_an_image_falls_back_to_full(regen_env):
    env, captured = regen_env
    assert reskin.regenerate_asset("rid", "goblin", "redder", mode="img2img")
    assert captured["init"] is None
