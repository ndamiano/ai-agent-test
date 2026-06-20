import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.ir_assemble import assemble_ir
from maestro.engines import compile_for
from web.compiler import compile_web
from renpy.compiler import compile_renpy


def _write_vn(run_dir: Path, *, dangling=False, broken=False):
    (run_dir / "premise.json").write_text(json.dumps({
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
        n1["lines"] = "not a list"  # schema-invalid
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


def test_compile_web_writes_project(tmp_path):
    _write_vn(tmp_path)
    result = compile_web(tmp_path, distribute=False)
    assert result["ok"], result.get("reason")

    out = tmp_path / "game_output"
    assert (out / "index.html").exists()
    assert (out / "engine.js").exists()
    assert (out / "style.css").exists()

    game = json.loads((out / "game.json").read_text())
    expected = assemble_ir({
        "premise": json.loads((tmp_path / "premise.json").read_text()),
        "asset_manifest": json.loads((tmp_path / "asset_manifest.json").read_text()),
        "nodes": json.loads((tmp_path / "nodes.json").read_text()),
    }, "vn")
    assert game == expected

    # placeholder art for every referenced image, so it renders with zero real assets
    assert (out / "images" / "room.png").exists()
    assert (out / "images" / "al.png").exists()


def test_distribute_zips(tmp_path):
    _write_vn(tmp_path)
    result = compile_web(tmp_path, distribute=True)
    assert result["ok"]
    assert Path(result["dist_path"]).exists()


def test_dangling_reference_fails_gate(tmp_path):
    _write_vn(tmp_path, dangling=True)
    result = compile_web(tmp_path, distribute=False)
    assert not result["ok"]
    assert "ghost" in " ".join(result["lint_errors"])


def test_schema_invalid_fails_gate(tmp_path):
    _write_vn(tmp_path, broken=True)
    result = compile_web(tmp_path, distribute=False)
    assert not result["ok"]
    assert result["lint_error_count"]


def test_missing_components_fails(tmp_path):
    result = compile_web(tmp_path, distribute=False)
    assert not result["ok"]
    assert "missing components" in result["reason"]


def test_engine_dispatch():
    assert compile_for("web") is compile_web
    assert compile_for("renpy") is compile_renpy
    assert compile_for("anything-else") is compile_renpy
