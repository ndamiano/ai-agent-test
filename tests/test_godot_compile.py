import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.ir_assemble import assemble_ir
from maestro.ir_crossref import crossref_errors
from maestro.engines import compile_for
from godot.compiler import compile_godot
from godot.ir_compiler import write_godot_project

_SCHEMA = Path(__file__).parent.parent / "docs" / "game_ir.schema.json"
_RUNTIME_FILES = ("project.godot", "Main.tscn", "Game.gd", "ir.gd", "vn.gd", "pnc.gd", "combat.gd")


def _write_vn(run_dir: Path, *, dangling=False, broken=False):
    (run_dir / "characters.json").write_text(json.dumps({
        "characters": [{"id": "al", "name": "Al"}, {"id": "bo", "name": "Bo"}]
    }))
    (run_dir / "asset_manifest.json").write_text(json.dumps({
        "backgrounds": [{"id": "bg_room", "image_file": "room.png"}],
        "characters": [{"id": "al", "image_file": "al.png"}],
    }))
    n1_end = {"type": "jump", "target": "ghost" if dangling else "n2"}
    n1 = {"location": "bg_room",
          "lines": [{"speaker": "al", "text": "hi"}, {"speaker": None, "text": "narr"}],
          "end": n1_end}
    if broken:
        n1["lines"] = "not a list"
    (run_dir / "nodes.json").write_text(json.dumps({
        "node_ids": ["n1", "n2"],
        "nodes": {
            "n1": n1,
            "n2": {"lines": [{"speaker": "bo", "text": "bye", "effects": [{"set_flag": "f1"}]}],
                   "end": {"type": "end", "ending": "done"}},
        },
        "flags": ["f1"], "start": "n1",
    }))
    (run_dir / "spec.json").write_text(json.dumps({"genre": "vn"}))


def _rpg_example() -> dict:
    schema = json.loads(_SCHEMA.read_text())
    return next(ex for ex in schema["examples"] if ex["genre"] == "rpg")


def test_compile_godot_writes_project(tmp_path):
    _write_vn(tmp_path)
    result = compile_godot(tmp_path, distribute=False)
    assert result["ok"], result.get("reason")

    out = tmp_path / "godot_output"
    for f in _RUNTIME_FILES:
        assert (out / f).exists(), f

    game = json.loads((out / "game.json").read_text())
    expected = assemble_ir({
        "characters": json.loads((tmp_path / "characters.json").read_text()),
        "asset_manifest": json.loads((tmp_path / "asset_manifest.json").read_text()),
        "nodes": json.loads((tmp_path / "nodes.json").read_text()),
    })
    assert game == expected
    assert (out / "images" / "room.png").exists()
    assert (out / "images" / "al.png").exists()


def test_distribute_zips(tmp_path):
    _write_vn(tmp_path)
    result = compile_godot(tmp_path, distribute=True)
    assert result["ok"]
    assert Path(result["dist_path"]).exists()


def test_dangling_reference_fails_gate(tmp_path):
    _write_vn(tmp_path, dangling=True)
    result = compile_godot(tmp_path, distribute=False)
    assert not result["ok"]
    assert "ghost" in " ".join(result["lint_errors"])


def test_schema_invalid_fails_gate(tmp_path):
    _write_vn(tmp_path, broken=True)
    result = compile_godot(tmp_path, distribute=False)
    assert not result["ok"]
    assert result["lint_error_count"]


def test_missing_components_fails(tmp_path):
    result = compile_godot(tmp_path, distribute=False)
    assert not result["ok"]
    assert "missing components" in result["reason"]


def test_combat_ir_lifts_into_project(tmp_path):
    """Godot's reason to exist: it projects combat the other engines stub. The schema's turn_based
    crypt example is a valid, complete IR; write it straight into a project (the v1 combat path,
    since no module authors encounters on disk yet) and prove the fight data survives."""
    ir = _rpg_example()
    assert crossref_errors(ir) == []

    out = tmp_path / "godot_output"
    write_godot_project(ir, out)

    game = json.loads((out / "game.json").read_text())
    assert game["encounters"][0]["id"] == "enc_crypt"
    assert game["combatants"] and game["abilities"] and game["stats"]
    assert (out / "combat.gd").exists()
    # arena + combatant sprite placeholders rendered so it plays with zero real art
    assert (out / "images" / "bg_crypt.png").exists()


def test_engine_dispatch():
    assert compile_for("godot") is compile_godot
