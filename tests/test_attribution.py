"""Cross-component failure attribution: a check may route its failure to the component that can
FIX it, not the one that declared it. The motivating bug: a dangling card-match opponent surfaced
only at the navigation spine's compile gate (place tools only), dead-looping. Now the cheap,
attributed `crossref` check routes a reference error to the slice that owns it."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import maestro.discrete  # noqa: F401 — registers modules + presets
from maestro.spec import Spec
from maestro.state import RunState
from maestro.validate import run_check, validate
from maestro.ir_crossref import slice_token, is_reference_kind
from maestro.modules import compose


# ── the path/kind primitives attribution routes on ────────────────────────────

def test_slice_token_extracts_routing_key():
    assert slice_token("card_matches[m1].opponent") == "card_matches"
    assert slice_token("nodes[greet].end.target") == "nodes"
    assert slice_token("places[hall].interactables[h1].action.node") == "places"
    assert slice_token("start.node") == "start.node"      # split one deeper: node vs place differ
    assert slice_token("start.place") == "start.place"
    assert slice_token("goal") == "goal"


def test_reference_vs_declaration_kinds():
    # reference (repoint to fix) routes to the slice; declaration (declare to fix) stays on spine
    assert is_reference_kind("character") and is_reference_kind("node") and is_reference_kind("place")
    assert not is_reference_kind("variable")
    assert not is_reference_kind("flag")
    assert not is_reference_kind("item")


def test_card_ante_composition_maps_card_matches_to_matches():
    owner = compose(("cast", "assets", "dialogue_npc", "navigation",
                     "economy", "card_play", "goal_endless")).slice_owner
    assert owner["card_matches"] == "matches"
    assert owner["places"] == "places"
    assert owner["nodes"] == "nodes"


# ── _check_crossref routes references to the owning component ──────────────────

def _card_components():
    return {
        "premise": {"central_question": "Out-bluff the house?",
                    "characters": [{"id": "gambler", "name": "The Gambler"}]},
        "asset_manifest": {"backgrounds": [{"id": "bg", "image_file": "b.png"}],
                           "characters": [{"id": "gambler", "image_file": "g.png"}], "cgs": []},
        "nodes": {"node_ids": ["greet"], "nodes": {
            "greet": {"lines": [{"speaker": "gambler", "text": "Hi."}], "end": {"type": "return"}}}},
        "places": {"start_place": "saloon", "place_ids": ["saloon"],
                   "variables": [{"id": "gold", "default": 100}], "flags": ["won"],
                   "goal": {"type": "flag", "id": "won"},
                   "places": {"saloon": {"kind": "room", "background": "bg", "interactables": [
                       {"id": "h_table", "label": "table",
                        "position": {"rect": {"x": 1, "y": 1, "w": 1, "h": 1}},
                        "action": {"type": "play_match", "match": "match_gambler"}}]}}},
        "matches": {"match_ids": ["match_gambler"], "matches": {
            "match_gambler": {"card_model": "blackjack", "deck_model": "standard_52",
                              "opponent": "gambler", "ante": {"var": "gold", "amount": 50},
                              "rounds": 1,
                              "on_win": {"effects": [{"add_var": {"var": "gold", "delta": 50}},
                                                     {"set_flag": "won"}], "end": {"type": "return"}},
                              "on_lose": {"effects": [{"add_var": {"var": "gold", "delta": -50}}],
                                          "end": {"type": "return"}}}}},
    }


def _run_dir(tmp_path, components):
    state = RunState(tmp_path)
    state.write_spec({"genre": "card_ante", "engine": "web", "frozen": True})
    for cid, content in components.items():
        state.write_component(cid, content)
    return state


def test_crossref_routes_bad_opponent_to_matches(tmp_path):
    comps = _card_components()
    comps["matches"]["matches"]["match_gambler"]["opponent"] = "specter"  # not a character
    state = _run_dir(tmp_path, comps)
    ok, detail, attributions = run_check({"type": "crossref"}, state.load_artifact(), state.run_dir)
    assert ok is False
    routed = [a for a in attributions if "specter" in a["detail"]]
    assert routed and routed[0]["component_id"] == "matches"


def test_crossref_leaves_undeclared_var_on_spine(tmp_path):
    comps = _card_components()
    comps["matches"]["matches"]["match_gambler"]["ante"]["var"] = "doubloons"  # never declared
    state = _run_dir(tmp_path, comps)
    ok, detail, attributions = run_check({"type": "crossref"}, state.load_artifact(), state.run_dir)
    assert ok is False
    routed = [a for a in attributions if "doubloons" in a["detail"]]
    # a declaration error (declare via set_places_meta) stays on the declaring spine, not matches
    assert routed and routed[0]["component_id"] is None


def test_crossref_clean_when_all_refs_resolve(tmp_path):
    state = _run_dir(tmp_path, _card_components())
    ok, _, _ = run_check({"type": "crossref"}, state.load_artifact(), state.run_dir)
    assert ok is True


# ── validate() lands the failure on matches, and matches is therefore not "done" ──

def _card_spec():
    return Spec({"frozen": True, "components": [
        {"id": "premise", "done_conditions": [
            {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]}]},
        {"id": "matches", "done_conditions": [
            {"type": "count", "path": "matches.match_ids", "min": 1}]},
        {"id": "places", "done_conditions": [
            {"type": "crossref"}]},   # the spine gate; routes references elsewhere
    ]})


def test_validate_attaches_opponent_failure_to_matches_not_places(tmp_path):
    comps = _card_components()
    comps["matches"]["matches"]["match_gambler"]["opponent"] = "specter"
    state = _run_dir(tmp_path, comps)
    failures = validate(_card_spec(), state)
    opp = [f for f in failures if "specter" in (f.get("detail") or "")]
    assert opp and opp[0]["component_id"] == "matches"   # NOT "places" (the declaring spine)


def test_single_component_validate_surfaces_routed_failure(tmp_path):
    # The lock check asks "is matches done?" via validate(component_id='matches'). A reference error
    # routed here must surface so the component does NOT lock (else edit_match would be refused).
    comps = _card_components()
    comps["matches"]["matches"]["match_gambler"]["opponent"] = "specter"
    state = _run_dir(tmp_path, comps)
    matches_fail = validate(_card_spec(), state, component_id="matches")
    assert any("specter" in (f.get("detail") or "") for f in matches_fail)
    # and a clean artifact leaves matches with nothing routed to it
    clean = _run_dir(tmp_path, _card_components())
    assert validate(_card_spec(), clean, component_id="matches") == []
