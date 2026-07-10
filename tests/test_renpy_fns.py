"""Tests for the live renpy build helpers (no LLM calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# _copy_templates — seeds missing gitignored assets from the SDK
# ---------------------------------------------------------------------------

def test_copy_templates_seeds_gui_from_sdk(tmp_path, monkeypatch):
    import renpy.renpy_builder as rb

    sdk_game = tmp_path / "sdk" / "the_question" / "game"
    (sdk_game / "gui").mkdir(parents=True)
    (sdk_game / "gui" / "frame.png").write_bytes(b"\x89PNG fake")
    (sdk_game / "screens.rpy").write_text("# screens")
    (sdk_game / "gui.rpy").write_text("# gui")

    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "gui.rpy").write_text("# customized gui")
    monkeypatch.setattr(rb, "TEMPLATES_DIR", str(templates))

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    rb._copy_templates(str(game_dir), str(tmp_path / "sdk"))

    # missing pieces seeded from SDK, existing customization untouched
    assert (templates / "gui" / "frame.png").exists()
    assert (templates / "screens.rpy").read_text() == "# screens"
    assert (templates / "gui.rpy").read_text() == "# customized gui"
    # and copied into the game
    assert (game_dir / "gui" / "frame.png").exists()
    assert (game_dir / "screens.rpy").exists()


def test_write_options_rpy_escapes_title(tmp_path):
    from renpy.renpy_builder import write_options_rpy

    write_options_rpy(str(tmp_path), 'The "Last" Stand \\')
    content = (tmp_path / "options.rpy").read_text()
    assert 'define config.name = "The \\"Last\\" Stand \\\\"' in content
    assert 'build.name = "The_Last_Stand_"' in content


def test_write_options_about_and_menu_background(tmp_path):
    from renpy.renpy_builder import write_options_rpy

    write_options_rpy(str(tmp_path), "T", about='A "tense" night.', menu_bg="title_card.png")
    content = (tmp_path / "options.rpy").read_text()
    assert 'define gui.about = "A \\"tense\\" night."' in content
    # title card absent on disk -> no menu override emitted
    assert "main_menu_background" not in content

    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "title_card.png").write_bytes(b"png")
    write_options_rpy(str(tmp_path), "T", about="x", menu_bg="title_card.png")
    content = (tmp_path / "options.rpy").read_text()
    assert 'gui.main_menu_background = "images/title_card.png"' in content
    assert 'gui.game_menu_background = "images/title_card.png"' in content
    assert "init 999 python" in content


def test_screens_template_has_no_foreign_font_or_stock_credits():
    from renpy.renpy_builder import TEMPLATES_DIR
    import os
    screens = open(os.path.join(TEMPLATES_DIR, "screens.rpy")).read()
    assert "SourceHanSansLite" not in screens
    assert "Mugenjohncel" not in screens
    assert "gui.about" in screens


def test_copy_templates_no_sdk_still_warns_not_crashes(tmp_path, monkeypatch):
    import renpy.renpy_builder as rb

    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(rb, "TEMPLATES_DIR", str(templates))

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    rb._copy_templates(str(game_dir), "")
    assert not (game_dir / "gui").exists()


# ---------------------------------------------------------------------------
# generate_images — placeholder fallback when ComfyUI fails
# ---------------------------------------------------------------------------

def test_generate_images_writes_placeholders_on_failure(tmp_path, monkeypatch):
    import tools.comfyui_tools as comfyui_tools
    from renpy.fns import generate_images

    import contextlib
    monkeypatch.setattr(comfyui_tools, "vram_bracket", contextlib.nullcontext)  # no live ComfyUI/LM Studio
    monkeypatch.setattr(
        comfyui_tools, "run_jobs",
        lambda jobs: [{"success": False, "error": "no comfyui"} for _ in jobs],
    )

    inputs = {
        "premise": {"characters": [{"id": "alex", "name": "Alex", "appearance": "tall"}]},
        "asset_manifest": {
            "backgrounds": [{"id": "bg_dock", "image_file": "dock.png", "description": "a dock"}],
            "characters":  [{"id": "alex", "name": "Alex", "image_file": "alex.png"}],
            "cgs":         [{"id": "cg_finale", "image_file": "cg_finale.png", "description": "finale"}],
            "title_card":  {"image_file": "title_card.png", "description": "title"},
        },
    }

    result = generate_images(inputs, tmp_path)

    assert result["status"] == "ok"
    assert result["generated"] == []
    assert {f["file"] for f in result["failed"]} == {"dock.png", "alex.png", "cg_finale.png", "title_card.png"}
    images_dir = tmp_path / "game_output" / "game" / "images"
    for name in ("dock.png", "alex.png", "cg_finale.png", "title_card.png"):
        png = (images_dir / name).read_bytes()
        assert png.startswith(b"\x89PNG")


def test_generate_images_tokens_only_for_walkable_games(tmp_path, monkeypatch):
    import tools.comfyui_tools as comfyui_tools
    from renpy.fns import generate_images

    import contextlib
    monkeypatch.setattr(comfyui_tools, "vram_bracket", contextlib.nullcontext)
    monkeypatch.setattr(comfyui_tools, "run_jobs",
                        lambda jobs: [{"success": False, "error": "no comfyui"} for _ in jobs])

    # A walkable game reconciles a token stub per character (derived at write time; generate_images
    # is a pure consumer of the reconciled manifest).
    inputs = {
        "characters": {"characters": [{"id": "kae", "name": "Kae"}]},
        "places": {"places": {"z1": {"kind": "world_map", "tiles": {"rows": ["."]},
                                     "interactables": []}}},
    }
    result = generate_images(inputs, tmp_path)
    files = {f["file"] for f in result["failed"]}
    assert "kae_token.png" in files                       # walkable game queues a token
    # a failed token writes NO placeholder — the overworld's colour-dot fallback covers it
    assert not (tmp_path / "game_output" / "game" / "images" / "kae_token.png").exists()

    inputs_vn = {"characters": {"characters": [{"id": "kae", "name": "Kae"}]}}
    result_vn = generate_images(inputs_vn, tmp_path)
    assert "kae_token.png" not in {f["file"] for f in result_vn["failed"]}


def test_build_token_job_is_square_chibi():
    from tools.comfyui_tools import build_token_job
    # the saved styled prompt (chibi framing rides in asset_token.txt) leads the positive
    job = build_token_job({"id": "kae", "description": "a tired knight in dented armor"})
    wf = job["workflow_override"]
    assert wf["28"]["inputs"]["width"] == wf["28"]["inputs"]["height"] == 832
    assert job["prompt"].startswith("a tired knight in dented armor")


def test_overworld_prefers_token_and_talk_markers_use_tokens():
    src = open("godot/runtime/overworld.gd").read() if __import__("os").path.exists(
        "godot/runtime/overworld.gd") else open("../src/godot/runtime/overworld.gd").read()
    assert '_token.png" % cid' in src
    assert 'atype == "talk"' in src


def test_generate_images_queues_items_as_icon_jobs(tmp_path, monkeypatch):
    import tools.comfyui_tools as comfyui_tools
    from renpy.fns import generate_images

    import contextlib
    monkeypatch.setattr(comfyui_tools, "vram_bracket", contextlib.nullcontext)
    queued = []

    def fake_run_jobs(jobs):
        queued.extend(jobs)
        return [{"success": False, "error": "no comfyui"} for _ in jobs]

    monkeypatch.setattr(comfyui_tools, "run_jobs", fake_run_jobs)

    # Items derive from the inventory component (reconcile mirrors the catalogue into icon stubs);
    # generate_images consumes the reconciled manifest. The icon's subject is the AUTHORED visual
    # description (describe_asset) — never the item's narrative `examine`.
    inputs = {"items": {"items": [{"id": "item_key", "name": "key",
                                   "examine": "worn smooth by the hero's grip"}]},
              "asset_manifest": {"items": [{"id": "item_key", "image_file": "item_key.png",
                                            "description": "a rusty iron key"}]}}
    generate_images(inputs, tmp_path)

    assert len(queued) == 1
    assert queued[0]["prompt"].startswith("a rusty iron key")
    assert "worn smooth" not in queued[0]["prompt"]


# ---------------------------------------------------------------------------
# generate_single_asset — the per-asset browser's targeted regen, never the whole manifest
# ---------------------------------------------------------------------------

def test_generate_single_asset_only_runs_one_job(tmp_path, monkeypatch):
    import contextlib
    import tools.comfyui_tools as comfyui_tools
    from renpy.fns import generate_single_asset

    monkeypatch.setattr(comfyui_tools, "vram_bracket", contextlib.nullcontext)
    calls = []

    def fake_run_jobs(jobs):
        calls.extend(jobs)
        return [{"success": False, "error": "no comfyui"} for _ in jobs]

    monkeypatch.setattr(comfyui_tools, "run_jobs", fake_run_jobs)

    inputs = {"asset_manifest": {
        "backgrounds": [{"id": "bg_dock", "image_file": "dock.png", "description": "a dock"},
                        {"id": "bg_sea", "image_file": "sea.png", "description": "the sea"}],
        "characters": [], "cgs": [],
    }}

    result = generate_single_asset(inputs, tmp_path, "dock.png")

    assert len(calls) == 1                       # only the requested background's job ran
    assert result["failed"][0]["file"] == "dock.png"
    images_dir = tmp_path / "game_output" / "game" / "images"
    assert (images_dir / "dock.png").exists()    # placeholder fallback on failure
    assert not (images_dir / "sea.png").exists()  # the OTHER background is untouched


def test_generate_single_asset_places_the_generated_file(tmp_path, monkeypatch):
    import contextlib
    import tools.comfyui_tools as comfyui_tools
    from renpy.fns import generate_single_asset
    from utils.image import write_solid_png

    src = tmp_path / "src.png"
    write_solid_png(src, 4, 4, (10, 20, 30))
    monkeypatch.setattr(comfyui_tools, "vram_bracket", contextlib.nullcontext)
    monkeypatch.setattr(comfyui_tools, "run_jobs",
                        lambda jobs: [{"success": True, "saved_paths": [str(src)]} for _ in jobs])

    inputs = {"asset_manifest": {
        "backgrounds": [], "cgs": [],
        "characters": [{"id": "alex", "image_file": "alex.png", "description": "a hero"}],
    }}

    result = generate_single_asset(inputs, tmp_path, "alex.png")

    assert result["status"] == "ok"
    assert result["generated"] == ["alex.png"]
    out = tmp_path / "game_output" / "game" / "images" / "alex.png"
    assert out.read_bytes().startswith(b"\x89PNG")


def test_generate_single_asset_unknown_filename_is_an_error(tmp_path):
    from renpy.fns import generate_single_asset

    result = generate_single_asset({"asset_manifest": {"backgrounds": [], "characters": [], "cgs": []}},
                                   tmp_path, "nope.png")
    assert result["status"] == "error"
    assert "nope.png" in result["error"]


def test_generate_single_asset_feature_chains_its_mesh_when_hd2d(tmp_path, monkeypatch):
    import contextlib
    import tools.comfyui_tools as comfyui_tools
    import renpy.fns as fns_mod
    from utils.image import write_solid_png

    src = tmp_path / "src.png"
    write_solid_png(src, 4, 4, (1, 2, 3))
    monkeypatch.setattr(comfyui_tools, "vram_bracket", contextlib.nullcontext)
    monkeypatch.setattr(comfyui_tools, "run_jobs",
                        lambda jobs: [{"success": True, "saved_paths": [str(src)]} for _ in jobs])
    monkeypatch.setattr(comfyui_tools, "require_trellis", lambda: None)
    monkeypatch.setattr(fns_mod, "_run_mesh_pass",
                        lambda meshed, images_dir: {"feature_old_altar"})

    inputs = {"places": {"places": {"z1": {
        "kind": "world_map",
        "layout": {"features": [{"id": "f1", "kind": "building", "label": "Old Altar", "at": "north"}]},
        "footprints": {"f1": {"x": 0, "y": 0, "w": 1, "h": 1, "kind": "building", "label": "Old Altar"}},
        "tiles": {"rows": ["."]},
        "interactables": [],
    }}}}

    result = fns_mod.generate_single_asset(inputs, tmp_path, "feature_old_altar.png", presentation="hd2d")

    assert result["status"] == "ok"
    assert "feature_old_altar.png" in result["generated"]
    assert "feature_old_altar.glb" in result["generated"]     # the mesh rode along
    assert result["mesh_total"] == 1 and result["mesh_done"] == 1


def test_generate_single_asset_feature_skips_mesh_for_2d(tmp_path, monkeypatch):
    import contextlib
    import tools.comfyui_tools as comfyui_tools
    from renpy.fns import generate_single_asset
    from utils.image import write_solid_png

    src = tmp_path / "src.png"
    write_solid_png(src, 4, 4, (1, 2, 3))
    monkeypatch.setattr(comfyui_tools, "vram_bracket", contextlib.nullcontext)
    monkeypatch.setattr(comfyui_tools, "run_jobs",
                        lambda jobs: [{"success": True, "saved_paths": [str(src)]} for _ in jobs])

    inputs = {"places": {"places": {"z1": {
        "kind": "world_map",
        "layout": {"features": [{"id": "f1", "kind": "building", "label": "Old Altar", "at": "north"}]},
        "footprints": {"f1": {"x": 0, "y": 0, "w": 1, "h": 1, "kind": "building", "label": "Old Altar"}},
        "tiles": {"rows": ["."]},
        "interactables": [],
    }}}}

    result = generate_single_asset(inputs, tmp_path, "feature_old_altar.png", presentation="2d")

    assert result["status"] == "ok"
    assert result["generated"] == ["feature_old_altar.png"]   # no .glb for a 2d build
    assert "mesh_total" not in result
