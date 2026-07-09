import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonschema
from maestro.ir_assemble import assemble_ir
from maestro.ir_crossref import crossref_errors
from conftest import load_example

_SCHEMA = json.loads((Path(__file__).parent.parent / "docs" / "game_ir.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator({k: v for k, v in _SCHEMA.items() if k != "examples"})


def _vn_artifact():
    return {
        "characters": {"characters": [{"id": "al", "name": "Al"}, {"id": "bo", "name": "Bo"}]},
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
    ir = assemble_ir(_vn_artifact())
    assert ir["genre"] == "visual_novel"
    assert ir["start"] == {"node": "n1"}
    assert [n["id"] for n in ir["nodes"]] == ["n1", "n2"]
    assert ir["flags"] == ["f1"]
    _VALIDATOR.validate(ir)              # raises on invalid
    assert crossref_errors(ir) == []


def test_authoring_provenance_stripped_from_nodes():
    # `beat` + `storyline` are authoring-only stamps (drive the beats_realized done-condition);
    # the IR schema is additionalProperties:false, so both must be dropped at assemble.
    art = _vn_artifact()
    art["nodes"]["nodes"]["n1"]["beat"] = "beat_01"
    art["nodes"]["nodes"]["n1"]["storyline"] = "sl_main"
    ir = assemble_ir(art)
    assert all("beat" not in n and "storyline" not in n for n in ir["nodes"])
    _VALIDATOR.validate(ir)


def test_manifest_backgrounds_and_sprites_lifted():
    art = _vn_artifact()
    art["asset_manifest"] = {
        "backgrounds": [{"id": "bg_room", "image_file": "room.png", "description": "x"}],
        "characters": [{"id": "al", "image_file": "al.png"}],  # bo has no asset
    }
    ir = assemble_ir(art)
    _VALIDATOR.validate(ir)
    assert ir["backgrounds"] == [{"id": "bg_room", "image_file": "room.png"}]
    al = next(c for c in ir["characters"] if c["id"] == "al")
    bo = next(c for c in ir["characters"] if c["id"] == "bo")
    assert al["sprite"] == "al.png"
    assert "sprite" not in bo


def test_start_defaults_to_first_node():
    art = _vn_artifact()
    del art["nodes"]["start"]
    ir = assemble_ir(art)
    assert ir["start"] == {"node": "n1"}


def test_crossref_catches_dangling_ref_after_assemble():
    art = _vn_artifact()
    art["nodes"]["nodes"]["n1"]["end"] = {"type": "jump", "target": "ghost"}
    ir = assemble_ir(art)
    assert any("ghost" in e and "node" in e for e in crossref_errors(ir))


def test_example_roundtrips_through_components():
    ex = load_example("vn_crappy")
    art = {
        "characters": {"characters": ex["characters"]},
        "asset_manifest": {"backgrounds": [], "characters": []},
        "items": {"items": ex.get("items", [])},
        "nodes": {
            "node_ids": [n["id"] for n in ex["nodes"]],
            "nodes": {n["id"]: {k: v for k, v in n.items() if k != "id"} for n in ex["nodes"]},
            "flags": ex.get("flags", []),
            "variables": ex.get("variables", []),
            "start": ex["start"].get("node"),
        },
    }
    ir = assemble_ir(art)
    _VALIDATOR.validate(ir)
    assert crossref_errors(ir) == []
    assert len(ir["nodes"]) == len(ex["nodes"])


def test_hd2d_presentation_lifted_to_meta_and_schema_valid():
    art = _vn_artifact()
    art["spec"] = {"presentation": "hd2d"}
    ir = assemble_ir(art)
    assert ir["meta"]["presentation"] == "hd2d"
    _VALIDATOR.validate(ir)              # schema must permit meta.presentation


def test_2d_presentation_leaves_meta_clean():
    art = _vn_artifact()
    art["spec"] = {"presentation": "2d"}
    assert "presentation" not in assemble_ir(art).get("meta", {})
