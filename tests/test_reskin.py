"""Assets stage (reskin) — the code path that needs no LLM/ComfyUI: plan parsing, draw-file
detection, the draw rewrite extraction, manifest emission, soft-fail when the image server is down,
and staging carrying the skin across."""

import json

from maestro.codegen import reskin
from maestro.codegen.gates import game_dir, stage_for_play, RUNTIME_DIR


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


def test_reskin_file_extracts_ts_block():
    reply = "here you go:\n```ts\nexport const x = 1;\n```\ntrailing"
    assert reskin.reskin_file(lambda s, u, m: reply, "main.ts", "old", ["player"]).strip() \
        == "export const x = 1;"


def test_write_manifest_shape(tmp_path):
    _write_game(tmp_path)
    reskin.write_manifest(tmp_path, [{"id": "player", "prompt": "p", "w": 24, "h": 24},
                                     {"id": "enemy", "prompt": "e", "w": 16, "h": 16}])
    m = json.loads((game_dir(tmp_path) / "assets.json").read_text())
    assert m["sprites"][0] == {"id": "player", "file": "assets/player.png", "w": 24, "h": 24}
    assert m["sprites"][1]["file"] == "assets/enemy.png"


def test_generate_sprites_soft_fails_when_server_down(tmp_path, monkeypatch):
    _write_game(tmp_path)
    import tools.comfyui_tools as ct
    monkeypatch.setattr(ct, "run_jobs", lambda jobs: (_ for _ in ()).throw(ConnectionError("down")))
    got = reskin.generate_sprites(tmp_path, [{"id": "player", "prompt": "p", "w": 24, "h": 24}])
    assert got == set()                        # no crash, no files
    assert (game_dir(tmp_path) / "assets").exists()


def test_autocrop_tightens_to_opaque_subject(tmp_path):
    from PIL import Image
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))          # fully transparent
    for x in range(40, 60):
        for y in range(40, 60):
            im.putpixel((x, y), (255, 0, 0, 255))             # 20x20 opaque subject in the middle
    p = tmp_path / "s.png"
    im.save(p)
    reskin._autocrop(p, pad_frac=0.0)
    assert Image.open(p).size == (20, 20)                     # cropped to the subject, margins gone


def test_autocrop_noop_on_fully_transparent(tmp_path):
    from PIL import Image
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
        import shutil
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
    assert m["meshes"][0] == {"id": "player", "file": "assets/player.glb"}
    assert m["meshes"][1]["file"] == "assets/crystal.glb"


def test_generate_meshes_soft_fails_when_backend_down(tmp_path, monkeypatch):
    _write_3d_game(tmp_path)
    import tools.comfyui_tools as ct
    monkeypatch.setattr(ct, "run_jobs", lambda jobs: (_ for _ in ()).throw(ConnectionError("down")))
    got = reskin.generate_meshes(tmp_path, [{"id": "player", "prompt": "p", "w": 1, "h": 2, "d": 1}])
    assert got == set()                        # no crash, no files
    assert (game_dir(tmp_path) / "assets").exists()


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
        import shutil
        shutil.rmtree(RUNTIME_DIR / "games" / slug, ignore_errors=True)


def test_trellis_batch_retries_after_unload(tmp_path, monkeypatch):
    """A 500 mid-batch is usually the leak-degraded pipeline — one unload + retry must recover the
    mesh instead of leaving a bare slab."""
    import tools.comfyui_tools as ct

    (tmp_path / "beast.png").write_bytes(b"png")
    out = tmp_path / "out"
    out.mkdir()

    calls = []

    def fake_post(url, body, ctype, timeout=None):
        calls.append(url)
        if url.endswith("/unload"):
            return b""
        if url.endswith("/generate") and len([c for c in calls if c.endswith("/generate")]) == 1:
            raise ConnectionError("HTTP Error 500")
        return b"glb-bytes"

    monkeypatch.setattr(ct, "_http_post_raw", fake_post)
    monkeypatch.setattr(ct, "_comfyui_free_vram", lambda ep: None)
    monkeypatch.setattr(ct, "_llm_get_loaded_model", lambda: None)

    done = ct.run_trellis_batch(str(tmp_path), str(out))
    assert done == {"beast"}
    assert (out / "beast.glb").read_bytes() == b"glb-bytes"
    assert [c for c in calls if c.endswith("/generate")] and len([c for c in calls if c.endswith("/unload")]) >= 2
