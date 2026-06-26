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

_ROOT = Path(__file__).parent.parent
_EXAMPLE = json.loads((_ROOT / "docs" / "examples" / "pnc_crappy.json").read_text())
_SCHEMA = json.loads((_ROOT / "docs" / "game_ir.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator({k: v for k, v in _SCHEMA.items() if k != "examples"})


def test_example_valid_and_clean():
    _VALIDATOR.validate(_EXAMPLE)
    assert crossref_errors(_EXAMPLE) == []


def test_compile_pnc_structure():
    out = compile_pnc(_EXAMPLE)
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
    # gated move
    assert "label hs_room_cell_hs_leave:" in out
    assert "    if door_open:\n        jump room_hall" in out
    # talk calls the dialogue node; win label exists
    assert "    call talk_warden" in out
    assert "label win:" in out
    assert 'warden "You won\'t find the key. I made sure of that."' in out


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


@pytest.mark.skipif(not _get_sdk_path(), reason="Ren'Py SDK not configured")
def test_compile_ir_pnc_lints_clean(tmp_path):
    _write_run(tmp_path)
    res = compile_ir(tmp_path, distribute=False)
    assert res["ok"], res["reason"]
    assert res["lint_error_count"] == 0
