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
