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

    monkeypatch.setattr(
        comfyui_tools, "generate_images_batch",
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
