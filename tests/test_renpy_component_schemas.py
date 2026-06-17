import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.component_schemas import validate_component, SCHEMAS, skeleton_guide
from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools


# ── schema validation ────────────────────────────────────────────────────────

def test_premise_requires_character_id_and_name():
    assert validate_component("premise", {"characters": [{"name": "Evelyn"}]})  # missing id
    assert "id" in validate_component("premise", {"characters": [{"name": "Evelyn"}]})
    assert validate_component("premise", {"characters": [{"id": "evelyn"}]})    # missing name
    assert validate_component("premise", {"characters": [{"id": "evelyn", "name": "Evelyn"}]}) is None


def test_asset_manifest_requires_list_keys_and_ids():
    # the shape the failing run produced (images/audio_files) is rejected
    bad = {"images": [{"filename": "x.png"}], "audio_files": ["a.mp3"]}
    assert validate_component("asset_manifest", bad)
    good = {"backgrounds": [{"id": "bg_office"}], "characters": [{"id": "evelyn"}], "cgs": []}
    assert validate_component("asset_manifest", good) is None


def test_node_scripts_consistency():
    assert validate_component("node_scripts", {"node_ids": ["start"], "scripts": {"start": "x"}})  # 'start' banned
    assert validate_component("node_scripts", {"node_ids": ["s1"], "scripts": {}})  # missing entry
    ok = {"node_ids": ["s1"], "scripts": {"s1": "label s1:\n    return"}}
    assert validate_component("node_scripts", ok) is None


def test_unknown_component_is_unconstrained():
    assert validate_component("whatever", {"anything": 1}) is None


def test_skeleton_guide_lists_components():
    guide = skeleton_guide()
    assert "premise" in guide and "asset_manifest" in guide and "node_scripts" in guide


def test_premise_skeleton_advertises_character_core():
    # The skeleton is what the model fills in, so it must show the portable core fields.
    guide = skeleton_guide(["premise"])
    for field in ("voice", "temperament", "drive", "history", "competencies", "example_lines"):
        assert f'"{field}"' in guide


# ── write_component enforces injected schemas ────────────────────────────────

def _frozen_spec():
    return Spec({"frozen": True, "components": [{"id": "premise", "done_conditions": []}]})


def test_write_component_rejects_invalid_shape(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_frozen_spec(), state, schemas=SCHEMAS)

    res = tools["write_component"]("premise", {"characters": [{"name": "NoId"}]})
    assert res["ok"] is False and "id" in res["error"]
    assert state.read_component("premise") is None    # not persisted

    res = tools["write_component"]("premise", {"characters": [{"id": "a", "name": "A"}]})
    assert res["ok"] is True
    assert state.read_component("premise") is not None


def test_write_component_without_schemas_is_unconstrained(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_frozen_spec(), state)   # no schemas injected
    assert tools["write_component"]("premise", {"anything": 1})["ok"] is True


def test_write_node_rejects_start_id(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_frozen_spec(), state, schemas=SCHEMAS)
    assert tools["write_node"]("start", "label start:\n    return")["ok"] is False
    assert tools["write_node"]("scene_01", "")["ok"] is False     # empty content
    assert tools["write_node"]("scene_01", "label scene_01:\n    return")["ok"] is True
