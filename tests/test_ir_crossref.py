import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.ir_crossref import crossref_errors

_SCHEMA = Path(__file__).parent.parent / "docs" / "game_ir.schema.json"


def _base():
    """A minimal IR where every reference resolves."""
    return {
        "version": "0.1", "genre": "point_and_click",
        "characters": [{"id": "warden", "name": "The Warden"}],
        "items": [{"id": "item_key", "name": "Key"}],
        "flags": ["door_open"],
        "variables": [{"id": "trust", "default": 0}],
        "start": {"place": "room_a"},
        "places": [{"id": "room_a", "kind": "room", "interactables": [
            {"id": "hs_warden", "action": {"type": "talk", "node": "talk_warden"}},
        ]}],
        "nodes": [{"id": "talk_warden",
                   "lines": [{"speaker": "warden", "text": "hi"}],
                   "end": {"type": "return"}}],
    }


def test_canonical_examples_resolve():
    examples = json.loads(_SCHEMA.read_text())["examples"]
    for e in examples:
        assert crossref_errors(e) == [], f"{e['genre']}: {crossref_errors(e)}"


def test_clean_base_has_no_errors():
    assert crossref_errors(_base()) == []


def test_null_speaker_is_narration_ok():
    ir = _base()
    ir["nodes"][0]["lines"][0]["speaker"] = None
    assert crossref_errors(ir) == []


# --- dangling-reference detection over _base() --------------------------------
# WHY: every one of these was a distinct `test_bad_*` proving crossref names both
# the offending token and the ref kind. They are one detector exercised over many
# paths; folding them into rows keeps the coverage and kills the clone bloat.
# A row is (mutate_fn, [(token, kind), ...], exact_count): mutate a FRESH _base(),
# then for each pair assert some error mentions both token and kind (kind=None =>
# token alone, for the sites crossref reports without a kind word). exact_count
# pins the total error count where a case must prove no over-reporting.

def _m_speaker(ir):
    ir["nodes"][0]["lines"][0]["speaker"] = "ghost"


def _m_talk_node(ir):
    ir["places"][0]["interactables"][0]["action"]["node"] = "nope"


def _m_jump_target(ir):
    ir["nodes"][0]["end"] = {"type": "jump", "target": "missing"}


def _m_menu_target(ir):
    ir["nodes"][0]["end"] = {"type": "menu", "choices": [
        {"text": "go", "target": "phantom"}]}


def _m_move_target(ir):
    ir["places"][0]["interactables"][0]["action"] = {"type": "move", "target": "void"}


def _m_take_item(ir):
    ir["places"][0]["interactables"][0]["action"] = {"type": "take", "item": "ghost_item"}


def _m_goal_item(ir):
    ir["goal"] = {"item": "phantom_item"}


def _m_goal_flag(ir):
    ir["goal"] = {"flag": "unknown_flag"}


def _m_effect_flag(ir):
    ir["nodes"][0]["lines"][0]["effects"] = [{"set_flag": "phantom_flag"}]


def _m_goal_var(ir):
    ir["goal"] = {"var": "missing_var", "op": ">", "value": 1}


def _m_var_operand(ir):
    ir["goal"] = {"var": "trust", "op": ">", "value": {"var": "ghost_var"}}


def _m_effect_var(ir):
    ir["nodes"][0]["lines"][0]["effects"] = [{"add_var": {"var": "nope_var", "delta": 1}}]


def _m_use_clauses(ir):
    ir["places"][0]["interactables"][0]["action"] = {
        "type": "use",
        "clauses": [{"requires": {"item": "no_item"},
                     "outcome": {"effects": [{"set_flag": "no_flag"}]}}],
        "fallback": {"effects": [{"add_var": {"var": "no_var", "delta": -1}}]},
    }


def _m_start_place(ir):
    ir["start"] = {"place": "nowhere"}


def _m_start_node(ir):
    ir["start"] = {"node": "no_node"}


def _m_nested_condition(ir):
    ir["goal"] = {"all": [{"flag": "door_open"},
                          {"any": [{"item": "item_key"}, {"flag": "ghost_flag"}]}]}


_BASE_CASES = [
    ("bad_speaker", _m_speaker, [("ghost", "character")], None),
    ("bad_talk_node", _m_talk_node, [("nope", "node")], None),
    ("bad_jump_target", _m_jump_target, [("missing", "node")], None),
    ("bad_menu_target", _m_menu_target, [("phantom", "node")], None),
    ("bad_move_target", _m_move_target, [("void", "place")], None),
    ("bad_take_item", _m_take_item, [("ghost_item", "item")], None),
    ("bad_goal_item", _m_goal_item, [("phantom_item", "item")], None),
    ("bad_goal_flag", _m_goal_flag, [("unknown_flag", "flag")], None),
    ("bad_effect_flag", _m_effect_flag, [("phantom_flag", "flag")], None),
    ("bad_goal_var", _m_goal_var, [("missing_var", "variable")], None),
    ("bad_var_operand", _m_var_operand, [("ghost_var", "variable")], None),
    ("bad_effect_var", _m_effect_var, [("nope_var", "variable")], None),
    ("use_clauses_and_fallback", _m_use_clauses,
     [("no_item", None), ("no_flag", None), ("no_var", None)], None),
    ("bad_start_place", _m_start_place, [("nowhere", "start.place")], None),
    ("bad_start_node", _m_start_node, [("no_node", "start.node")], None),
    ("nested_composite_condition", _m_nested_condition, [("ghost_flag", None)], 1),
]


@pytest.mark.parametrize(
    "mutate, expected, exact_count",
    [(m, exp, n) for _id, m, exp, n in _BASE_CASES],
    ids=[_id for _id, *_ in _BASE_CASES],
)
def test_base_ref_resolution(mutate, expected, exact_count):
    ir = _base()
    mutate(ir)
    errs = crossref_errors(ir)
    if exact_count is not None:
        assert len(errs) == exact_count, errs
    for token, kind in expected:
        if kind is None:
            assert any(token in e for e in errs), (token, errs)
        else:
            assert any(token in e and kind in e for e in errs), (token, kind, errs)


# --- combat references --------------------------------------------------------

def _combat():
    """A minimal rpg IR with combat where every reference resolves."""
    return {
        "version": "0.1", "genre": "rpg",
        "characters": [{"id": "hero", "name": "Hero"}],
        "flags": ["spared"],
        "combat_model": "turn_based",
        "stats": [
            {"id": "hp", "default": 20, "min": 0, "max": 20, "role": "resource_depletable"},
            {"id": "atk", "default": 4, "role": "modifier"},
        ],
        "statuses": [{"id": "poison", "name": "Poison",
                      "tick": [{"stat": "hp", "op": "damage", "formula": {"base": 1}}]}],
        "abilities": [{"id": "slash", "name": "Slash",
                       "targeting": {"shape": "single", "faction": "enemy"},
                       "effects": [{"stat": "hp", "op": "damage",
                                    "formula": {"base": 3, "scales_with": "atk"}}]}],
        "combatants": [{"id": "cb_hero", "character": "hero",
                        "stats": [{"stat": "hp", "value": 20}], "abilities": ["slash"]}],
        "encounters": [{"id": "enc1", "combatants": [{"ref": "cb_hero", "faction": "player"}],
                        "victory": {"all_defeated": "enemy"},
                        "on_victory": {"type": "jump", "target": "n1"}}],
        "start": {"place": "room1"},
        "places": [{"id": "room1", "kind": "interior", "interactables": [
            {"id": "hs", "action": {"type": "start_combat", "encounter": "enc1"}}]}],
        "nodes": [{"id": "n1", "lines": [{"speaker": "hero", "text": "won"}],
                   "end": {"type": "end"}}],
    }


def test_combat_base_resolves():
    assert crossref_errors(_combat()) == []


# --- dangling-reference detection over _combat() ------------------------------
# WHY: the combat IR grew its own family of `test_bad_*` clones; same detector,
# combat-shaped ref sites (combatant/ability/status/encounter/start_combat). Each
# original mutated ONE _combat() and asserted several tokens+kinds, so a row here
# carries a list of pairs; same fold contract as the _base cases above.

def _c_combatant(ir):
    ir["combatants"][0]["character"] = "nobody"
    ir["combatants"][0]["stats"][0]["stat"] = "no_stat"
    ir["combatants"][0]["abilities"] = ["no_ability"]


def _c_ability(ir):
    ir["abilities"][0]["cost"] = [{"stat": "ghost_stat", "amount": 2}]
    ir["abilities"][0]["effects"][0]["stat"] = "phantom_stat"
    ir["abilities"][0]["effects"][0]["formula"]["scales_with"] = "no_mod"


def _c_status_world(ir):
    ir["abilities"][0]["effects"] = [
        {"status": "no_status", "duration": 2},
        {"world": {"set_flag": "no_flag"}},
    ]


def _c_status_tick(ir):
    ir["statuses"][0]["tick"][0]["stat"] = "ghost_hp"


def _c_encounter(ir):
    ir["encounters"][0]["combatants"][0]["ref"] = "no_combatant"
    ir["encounters"][0]["defeat"] = {"when": {"flag": "ghost_flag"}}
    ir["encounters"][0]["on_victory"] = {"type": "jump", "target": "no_node"}


def _c_start_combat(ir):
    ir["places"][0]["interactables"][0]["action"]["encounter"] = "no_enc"


_COMBAT_CASES = [
    ("bad_combatant_character_stat_ability", _c_combatant,
     [("nobody", "character"), ("no_stat", "stat"), ("no_ability", "ability")]),
    ("bad_ability_cost_effect_scaling", _c_ability,
     [("ghost_stat", None), ("phantom_stat", None), ("no_mod", None)]),
    ("bad_status_in_effect_and_world_bridge", _c_status_world,
     [("no_status", "status"), ("no_flag", "flag")]),
    ("bad_status_tick_stat", _c_status_tick, [("ghost_hp", "stat")]),
    ("bad_encounter_ref_condition_resolution", _c_encounter,
     [("no_combatant", "combatant"), ("ghost_flag", "flag"), ("no_node", "node")]),
    ("bad_start_combat_encounter_ref", _c_start_combat, [("no_enc", "encounter")]),
]


@pytest.mark.parametrize(
    "mutate, expected",
    [(m, exp) for _id, m, exp in _COMBAT_CASES],
    ids=[_id for _id, *_ in _COMBAT_CASES],
)
def test_combat_ref_resolution(mutate, expected):
    ir = _combat()
    mutate(ir)
    errs = crossref_errors(ir)
    for token, kind in expected:
        if kind is None:
            assert any(token in e for e in errs), (token, errs)
        else:
            assert any(token in e and kind in e for e in errs), (token, kind, errs)
