import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import maestro.discrete  # noqa: F401 — registers modules + presets
from maestro.modules import (Module, compose, modules_for, register_module, register_projection,
                             unprojectable, PRESETS)


def _types(baseline, cid):
    return {c["type"] for c in baseline.get(cid, [])}


# ── composition reproduces the per-genre baselines ────────────────────────────

def test_vn_composition_has_story_spine_floor():
    b = compose(PRESETS["vn"].modules).baseline
    assert {"reachable_from_start", "min_branches", "all_characters_speak",
            "each_node_min_lines", "compiles"} <= _types(b, "nodes")
    # premise carries the branching-cast contract (3+ chars, endings)
    assert {"count", "distinct"} <= _types(b, "premise")


def test_pnc_composition_has_navigation_floor_and_light_dialogue():
    b = compose(PRESETS["point_and_click"].modules).baseline
    assert {"places_reachable", "goal_reachable", "items_used", "compiles"} <= _types(b, "places")
    # nodes are supporting barks: a body floor + reference integrity, but NOT the heavy spine checks
    assert _types(b, "nodes") == {"each_node_min_lines", "node_targets_resolve"}
    assert "reachable_from_start" not in _types(b, "nodes")
    # premise stays minimal — no forced endings/3-char contract
    assert "distinct" not in _types(b, "premise")


def test_win_is_a_composed_mechanic_not_baked_into_navigation():
    # The win/goal contract is its own module family: an escape room demands a reachable win,
    # a wander-and-gamble game is open-ended — same navigation module, different goal module.
    pnc = _types(compose(PRESETS["point_and_click"].modules).baseline, "places")
    card = _types(compose(PRESETS["card_ante"].modules).baseline, "places")
    assert "goal_reachable" in pnc            # goal_flag
    assert "goal_reachable" not in card       # goal_endless — no win state
    # navigation alone (no goal module) carries no win check
    assert "goal_reachable" not in _types(compose(["navigation"]).baseline, "places")


def test_pnc_carries_build_order_deps_vn_does_not():
    assert compose(PRESETS["vn"].modules).deps == {}
    pnc = compose(PRESETS["point_and_click"].modules).deps
    assert pnc["places"] == ["premise", "asset_manifest", "nodes"]


# ── the merge: union each_has fields, raise min, never drop ────────────────────

def test_each_has_fields_union_across_modules():
    a = Module(id="m_a", baseline={"premise": [
        {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]}]})
    b = Module(id="m_b", baseline={"premise": [
        {"type": "each_has", "path": "premise.characters", "fields": ["name", "voice"]}]})
    register_module(a)
    register_module(b)
    merged = compose(["m_a", "m_b"]).baseline["premise"]
    assert len(merged) == 1                       # same (type, path) = one check
    assert merged[0]["fields"] == ["id", "name", "voice"]   # unioned, order-preserving


def test_min_raised_not_lowered():
    a = Module(id="m_lo", baseline={"nodes": [{"type": "count", "path": "nodes.node_ids", "min": 5}]})
    b = Module(id="m_hi", baseline={"nodes": [{"type": "count", "path": "nodes.node_ids", "min": 20}]})
    register_module(a)
    register_module(b)
    for order in (["m_lo", "m_hi"], ["m_hi", "m_lo"]):
        check = compose(order).baseline["nodes"][0]
        assert check["min"] == 20


# ── modules_for: explicit modules win, else the genre preset ───────────────────

def test_modules_for_prefers_explicit_then_preset():
    assert modules_for({"modules": ["cast"]}) == ("cast",)
    assert modules_for({"genre": "point_and_click"}) == PRESETS["point_and_click"].modules
    assert modules_for({}) == PRESETS["vn"].modules          # default


# ── the projection fail-fast guard ─────────────────────────────────────────────

def test_unprojectable_flags_projected_module_without_engine_projection():
    register_module(Module(id="m_proj", projected=True))
    assert unprojectable("nope_engine", ["m_proj"]) == ["m_proj"]
    register_projection("nope_engine", "m_proj", lambda ir: "")
    assert unprojectable("nope_engine", ["m_proj"]) == []
    # a non-projected module is never flagged
    register_module(Module(id="m_plain", projected=False))
    assert unprojectable("any_engine", ["m_plain"]) == []
