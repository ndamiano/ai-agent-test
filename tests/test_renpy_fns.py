"""Tests for the live renpy build helpers (no LLM calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from renpy.fns import _merge_cast_into_manifest


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

    inputs = {"asset_manifest": {
        "items": [{"id": "item_key", "description": "a rusty iron key"}],
    }}
    generate_images(inputs, tmp_path)

    assert len(queued) == 1
    assert "game item icon" in queued[0]["prompt"]
    assert "rusty iron key" in queued[0]["prompt"]


# ---------------------------------------------------------------------------
# _merge_cast_into_manifest — premise is the source of truth for the cast
# ---------------------------------------------------------------------------

def test_merge_backfills_empty_manifest_from_premise():
    premise = {"characters": [{"id": "jack", "name": "Jack", "voice": "gruff"},
                              {"id": "mara", "name": "Mara", "description": "informant"}]}
    merged = _merge_cast_into_manifest(premise, {"characters": []})
    ids = {c["id"] for c in merged["characters"]}
    assert ids == {"jack", "mara"}
    by_id = {c["id"]: c for c in merged["characters"]}
    assert by_id["jack"]["image_file"] == "jack.png"
    assert by_id["mara"]["description"] == "informant"


def test_merge_preserves_existing_manifest_entry_as_override():
    premise = {"characters": [{"id": "jack", "name": "Jack"}]}
    manifest = {"characters": [{"id": "jack", "image_file": "custom_jack.png", "description": "art note"}]}
    merged = _merge_cast_into_manifest(premise, manifest)
    assert len(merged["characters"]) == 1
    assert merged["characters"][0]["image_file"] == "custom_jack.png"


def test_merge_keeps_other_manifest_keys():
    premise = {"characters": [{"id": "jack", "name": "Jack"}]}
    manifest = {"backgrounds": [{"id": "bg_x"}], "characters": []}
    merged = _merge_cast_into_manifest(premise, manifest)
    assert merged["backgrounds"] == [{"id": "bg_x"}]
    assert {c["id"] for c in merged["characters"]} == {"jack"}
