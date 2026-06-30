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
_COMBAT_EXAMPLE = Path(__file__).parent.parent / "docs" / "examples" / "combat_game.json"
_RUNTIME_FILES = ("project.godot", "Main.tscn", "Game.gd", "ir.gd", "vn.gd", "pnc.gd",
                  "overworld.gd", "combat.gd")


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
    return json.loads(_COMBAT_EXAMPLE.read_text())


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


def test_combat_example_is_valid_ir():
    """The walkable combat showcase must be a complete, schema-valid IR with every reference
    resolving — it doubles as the spec's worked example, so a drift here is a doc+engine bug."""
    ir = _rpg_example()
    assert _schema_errors(ir) == []
    assert crossref_errors(ir) == []


def _schema_errors(ir: dict) -> list:
    import jsonschema
    schema = json.loads(_SCHEMA.read_text())
    v = jsonschema.Draft202012Validator({k: val for k, val in schema.items() if k != "examples"})
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in v.iter_errors(ir)]


def test_combat_ir_lifts_into_project(tmp_path):
    """Godot's reason to exist: it projects combat the other engines stub. Write the showcase IR
    straight into a project and prove the fight data + the walkable-world wiring survive."""
    ir = _rpg_example()
    out = tmp_path / "godot_output"
    write_godot_project(ir, out)

    game = json.loads((out / "game.json").read_text())
    assert {e["id"] for e in game["encounters"]} == {"enc_slimes", "enc_boss"}
    assert game["combatants"] and game["abilities"] and game["stats"]
    assert (out / "combat.gd").exists()
    assert (out / "overworld.gd").exists()
    # arena/map placeholders rendered so it plays with zero real art
    assert (out / "images" / "bg_crypt.png").exists() or (out / "images" / "crypt.png").exists()

    # The walkable world: RPG places carry a grid + walls, and a tile starts each fight.
    ov = next(p for p in game["places"] if p["id"] == "overworld")
    assert ov["kind"] == "world_map" and ov["grid"]["w"] > 0 and ov["impassable"]
    combat_tiles = [it for p in game["places"] for it in p["interactables"]
                    if it["action"]["type"] == "start_combat"]
    assert combat_tiles, "no tile starts combat"

    # Inventory <-> combat bridge: a potion item, a take tile that grants it, and an ability gated
    # on holding it that consumes it via a world remove_item — proves inventory flows into a fight.
    assert any(i["id"] == "potion" for i in game["items"])
    quaff = next(a for a in game["abilities"] if a["id"] == "quaff")
    assert quaff["requires"] == {"item": "potion"}
    assert any(e.get("world") == {"remove_item": "potion"} for e in quaff["effects"])


def test_engine_dispatch():
    assert compile_for("godot") is compile_godot
