import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.ir_compiler import compile_ir
from renpy.fns import _get_sdk_path
from conftest import load_example

_EXAMPLE = load_example("vn_crappy")


def _write_run(run_dir: Path):
    ex = _EXAMPLE
    (run_dir / "spec.json").write_text(json.dumps({"genre": "vn", "title": ex["meta"]["title"]}))
    (run_dir / "characters.json").write_text(json.dumps({"characters": ex["characters"]}))
    (run_dir / "asset_manifest.json").write_text(json.dumps({"backgrounds": [], "characters": []}))
    (run_dir / "nodes.json").write_text(json.dumps({
        "node_ids": [n["id"] for n in ex["nodes"]],
        "nodes": {n["id"]: {k: v for k, v in n.items() if k != "id"} for n in ex["nodes"]},
        "flags": ex.get("flags", []),
        "variables": ex.get("variables", []),
        "start": ex["start"].get("node"),
    }))
    if ex.get("items"):
        (run_dir / "items.json").write_text(json.dumps({"items": ex["items"]}))


def test_compile_ir_writes_script(tmp_path):
    _write_run(tmp_path)
    compile_ir(tmp_path, distribute=False)
    script = (tmp_path / "game_output" / "game" / "script.rpy").read_text()
    assert 'define al = Character("Al")' in script
    assert "label start:\n    jump n_intro" in script


def test_compile_ir_rejects_malformed_ir(tmp_path):
    _write_run(tmp_path)
    nodes = json.loads((tmp_path / "nodes.json").read_text())
    nodes["nodes"]["n_intro"]["lines"] = [{"speaker": "al"}]
    (tmp_path / "nodes.json").write_text(json.dumps(nodes))
    res = compile_ir(tmp_path, distribute=False)
    assert not res["ok"]
    assert res["reason"].startswith("invalid IR")


def test_compile_ir_rejects_walkable_places(tmp_path):
    pnc = load_example("pnc_crappy")
    (tmp_path / "spec.json").write_text(json.dumps({"title": pnc["meta"]["title"]}))
    (tmp_path / "characters.json").write_text(json.dumps({"characters": pnc["characters"]}))
    (tmp_path / "asset_manifest.json").write_text(json.dumps({"backgrounds": [], "characters": []}))
    (tmp_path / "nodes.json").write_text(json.dumps({
        "node_ids": [n["id"] for n in pnc["nodes"]],
        "nodes": {n["id"]: {k: v for k, v in n.items() if k != "id"} for n in pnc["nodes"]},
    }))
    places = {p["id"]: {k: v for k, v in p.items() if k != "id"} for p in pnc["places"]}
    places["room_hall"]["kind"] = "town"
    places["room_hall"]["tiles"] = {"rows": ["...", "..."],
                                    "legend": {".": {"role": "open", "theme": "stone"}}}
    (tmp_path / "places.json").write_text(json.dumps({
        "place_ids": [p["id"] for p in pnc["places"]],
        "places": places,
        "flags": pnc["flags"],
        "goal": pnc["goal"],
        "start_place": pnc["start"]["place"],
    }))
    (tmp_path / "items.json").write_text(json.dumps({"items": pnc["items"]}))
    res = compile_ir(tmp_path, distribute=False)
    assert not res["ok"]
    assert "walkable" in res["reason"]
    assert "room_hall" in res["reason"] and "town" in res["reason"]
    assert "godot" in res["reason"]


def test_compile_ir_reports_unresolved_refs(tmp_path):
    _write_run(tmp_path)
    nodes = json.loads((tmp_path / "nodes.json").read_text())
    nodes["nodes"]["n_intro"]["end"] = {"type": "jump", "target": "ghost"}
    (tmp_path / "nodes.json").write_text(json.dumps(nodes))
    res = compile_ir(tmp_path, distribute=False)
    assert not res["ok"]
    assert "unresolved references" in res["reason"] and "ghost" in res["reason"]


@pytest.mark.skipif(not _get_sdk_path(), reason="Ren'Py SDK not configured")
def test_compile_ir_lints_clean_on_sdk(tmp_path):
    _write_run(tmp_path)
    res = compile_ir(tmp_path, distribute=False)
    assert res["ok"], res["reason"]
    assert res["lint_error_count"] == 0
