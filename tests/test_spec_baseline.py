import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.spec_baseline import enforce_baseline
from maestro import spec_tools


def _types(comp):
    return {c.get("type") for c in comp["done_conditions"]}


def _node_comp(conditions):
    return {"components": [{"id": "nodes", "done_conditions": list(conditions)}]}


def test_adds_missing_structure_and_quality_checks():
    # The exact failure we hit: proposer emitted only count + refs + compiles.
    spec = _node_comp([
        {"type": "count", "path": "nodes.node_ids", "min": 20},
        {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
         "to": "nodes.node_ids"},
        {"type": "compiles"},
    ])
    enforce_baseline(spec)
    types = _types(spec["components"][0])
    assert {"each_node_min_lines", "reachable_from_start", "min_branches",
            "all_characters_speak"} <= types


def test_raises_min_to_floor_but_keeps_higher():
    spec = _node_comp([
        {"type": "count", "path": "nodes.node_ids", "min": 6},     # below floor → raised
        {"type": "each_node_min_lines", "min": 40},                       # above floor → kept
    ])
    enforce_baseline(spec)
    conds = {c["type"]: c for c in spec["components"][0]["done_conditions"]}
    assert conds["count"]["min"] == 20
    assert conds["each_node_min_lines"]["min"] == 40


def test_does_not_duplicate_existing_checks():
    spec = _node_comp([{"type": "reachable_from_start"}])
    enforce_baseline(spec)
    reach = [c for c in spec["components"][0]["done_conditions"]
             if c["type"] == "reachable_from_start"]
    assert len(reach) == 1


def test_idempotent():
    spec = _node_comp([])
    enforce_baseline(spec)
    once = list(spec["components"][0]["done_conditions"])
    enforce_baseline(spec)
    assert spec["components"][0]["done_conditions"] == once


def test_distinct_count_paths_do_not_collide():
    # premise has two `count` checks on different paths — both must survive.
    spec = {"components": [{"id": "premise", "done_conditions": []}]}
    enforce_baseline(spec)
    counts = {c["path"] for c in spec["components"][0]["done_conditions"]
              if c["type"] == "count"}
    assert counts == {"premise.characters", "premise.endings"}


def test_skips_components_not_in_spec():
    spec = {"components": [{"id": "premise", "done_conditions": []}]}
    enforce_baseline(spec)
    ids = {c["id"] for c in spec["components"]}
    assert ids == {"premise"}              # does not invent nodes/asset_manifest


def test_registry_idempotent_and_applied(monkeypatch):
    monkeypatch.setattr(spec_tools, "_SPEC_NORMALIZERS", [])
    spec_tools.register_spec_normalizer(enforce_baseline)
    spec_tools.register_spec_normalizer(enforce_baseline)   # second call is a no-op
    assert spec_tools._SPEC_NORMALIZERS == [enforce_baseline]

    spec = _node_comp([])
    spec_tools._normalize_spec(spec)
    assert "all_characters_speak" in _types(spec["components"][0])
