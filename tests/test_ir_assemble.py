import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonschema
from renpy.ir_assemble import assemble_ir
from maestro.ir_crossref import crossref_errors

_SCHEMA = json.loads((Path(__file__).parent.parent / "docs" / "game_ir.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator({k: v for k, v in _SCHEMA.items() if k != "examples"})


def _vn_artifact():
    return {
        "premise": {"characters": [{"id": "al", "name": "Al"}, {"id": "bo", "name": "Bo"}]},
        "asset_manifest": {"backgrounds": [], "characters": []},
        "nodes": {
            "node_ids": ["n1", "n2"],
            "nodes": {
                "n1": {"lines": [{"speaker": "al", "text": "hi"}, {"speaker": None, "text": "narr"}],
                       "end": {"type": "jump", "target": "n2"}},
                "n2": {"lines": [{"speaker": "bo", "text": "bye", "effects": [{"set_flag": "f1"}]}],
                       "end": {"type": "end", "ending": "done"}},
            },
            "flags": ["f1"],
            "variables": [{"id": "mood", "default": 0}],
            "start": "n1",
        },
    }


def test_assemble_vn_is_schema_valid_and_clean():
    ir = assemble_ir(_vn_artifact(), "vn")
    assert ir["genre"] == "visual_novel"
    assert ir["start"] == {"node": "n1"}
    assert [n["id"] for n in ir["nodes"]] == ["n1", "n2"]
    assert ir["flags"] == ["f1"]
    _VALIDATOR.validate(ir)              # raises on invalid
    assert crossref_errors(ir) == []


def test_start_defaults_to_first_node():
    art = _vn_artifact()
    del art["nodes"]["start"]
    ir = assemble_ir(art, "vn")
    assert ir["start"] == {"node": "n1"}


def test_crossref_catches_dangling_ref_after_assemble():
    art = _vn_artifact()
    art["nodes"]["nodes"]["n1"]["end"] = {"type": "jump", "target": "ghost"}
    ir = assemble_ir(art, "vn")
    assert any("ghost" in e and "node" in e for e in crossref_errors(ir))


def test_example_roundtrips_through_components():
    ex = json.loads((Path(__file__).parent.parent / "docs" / "examples" / "vn_crappy.json").read_text())
    art = {
        "premise": {"characters": ex["characters"]},
        "asset_manifest": {"backgrounds": [], "characters": []},
        "nodes": {
            "node_ids": [n["id"] for n in ex["nodes"]],
            "nodes": {n["id"]: {k: v for k, v in n.items() if k != "id"} for n in ex["nodes"]},
            "flags": ex.get("flags", []),
            "variables": ex.get("variables", []),
            "items": ex.get("items", []),
            "start": ex["start"].get("node"),
        },
    }
    ir = assemble_ir(art, "vn")
    _VALIDATOR.validate(ir)
    assert crossref_errors(ir) == []
    assert len(ir["nodes"]) == len(ex["nodes"])
