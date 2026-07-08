import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonschema
from renpy.ir_pnc import compile_pnc
from renpy.ir_compiler import compile_ir
from renpy.fns import _get_sdk_path
from maestro.ir_crossref import crossref_errors
from conftest import load_example

_ROOT = Path(__file__).parent.parent
_EXAMPLE = load_example("pnc_crappy")
_SCHEMA = json.loads((_ROOT / "docs" / "game_ir.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator({k: v for k, v in _SCHEMA.items() if k != "examples"})


def test_example_valid_and_clean():
    _VALIDATOR.validate(_EXAMPLE)
    assert crossref_errors(_EXAMPLE) == []


def test_compile_pnc_structure():
    out = compile_pnc(_EXAMPLE)
    # no ir["backgrounds"] map in the example -> filenames fall back to <id>.png
    assert 'image bg_cell = "images/bg_cell.png"' in out
    assert "screen room_cell():" in out
    assert 'add "bg_cell"' in out
    assert "use inventory_bar" in out
    assert 'action Call("hs_room_cell_hs_drawer")' in out
    # take hotspot guards the append
    assert '$ if "item_key" not in inventory: inventory.append("item_key")' in out
    # use clause + fallback
    assert 'if "item_key" in inventory:' in out
    assert "$ door_unlocked = True" not in out  # we set door_open, not a stray flag
    assert "$ door_open = True" in out
    # gated move pops the screen Call frame before jumping (no stack leak)
    assert "label hs_room_cell_hs_leave:" in out
    assert "    if door_open:\n        $ renpy.pop_call()\n        jump room_hall" in out
    # talk calls the dialogue node; win label exists
    assert "    call talk_warden" in out
    assert "label win:" in out
    assert 'warden "You won\'t find the key. I made sure of that."' in out


def test_backgrounds_map_resolves_image_files():
    ir = {**_EXAMPLE, "backgrounds": [{"id": "bg_cell", "image_file": "cell_interior.png"}]}
    out = compile_pnc(ir)
    assert 'image bg_cell = "images/cell_interior.png"' in out
    assert 'image bg_hall = "images/bg_hall.png"' in out


def test_win_ends_game():
    out = compile_pnc(_EXAMPLE)
    # The hotspot label was entered via the screen's Call(); the frame must be popped before
    # `jump win` or `label win:`'s final `return` resumes the room loop instead of ending.
    assert "label hs_room_hall_hs_win:\n    $ renpy.pop_call()\n    jump win" in out
    assert "label win:\n    scene black\n" in out
    assert 'centered "You won."' in out


def _write_run(run_dir: Path):
    ex = _EXAMPLE
    (run_dir / "spec.json").write_text(json.dumps({"genre": "point_and_click", "title": ex["meta"]["title"]}))
    (run_dir / "characters.json").write_text(json.dumps({"characters": ex["characters"]}))
    (run_dir / "asset_manifest.json").write_text(json.dumps({"backgrounds": [], "characters": []}))
    (run_dir / "nodes.json").write_text(json.dumps({
        "node_ids": [n["id"] for n in ex["nodes"]],
        "nodes": {n["id"]: {k: v for k, v in n.items() if k != "id"} for n in ex["nodes"]},
    }))
    (run_dir / "places.json").write_text(json.dumps({
        "place_ids": [p["id"] for p in ex["places"]],
        "places": {p["id"]: {k: v for k, v in p.items() if k != "id"} for p in ex["places"]},
        "flags": ex["flags"],
        "goal": ex["goal"],
        "start_place": ex["start"]["place"],
    }))
    (run_dir / "items.json").write_text(json.dumps({"items": ex["items"]}))


def test_compile_ir_dispatches_pnc(tmp_path):
    _write_run(tmp_path)
    compile_ir(tmp_path, distribute=False)
    script = (tmp_path / "game_output" / "game" / "script.rpy").read_text()
    assert "screen room_cell():" in script


def test_compile_ir_pnc_uses_manifest_background_files(tmp_path):
    _write_run(tmp_path)
    (tmp_path / "asset_manifest.json").write_text(json.dumps({
        "backgrounds": [{"id": "bg_cell", "image_file": "cell_interior.png",
                         "description": "a cell"}],
        "characters": [],
    }))
    compile_ir(tmp_path, distribute=False)
    script = (tmp_path / "game_output" / "game" / "script.rpy").read_text()
    assert 'image bg_cell = "images/cell_interior.png"' in script
    images = tmp_path / "game_output" / "game" / "images"
    assert (images / "cell_interior.png").exists()
    assert (images / "bg_hall.png").exists()


@pytest.mark.skipif(not _get_sdk_path(), reason="Ren'Py SDK not configured")
def test_compile_ir_pnc_lints_clean(tmp_path):
    _write_run(tmp_path)
    res = compile_ir(tmp_path, distribute=False)
    assert res["ok"], res["reason"]
    assert res["lint_error_count"] == 0
