import json
import sys
from pathlib import Path

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


def test_bad_speaker():
    ir = _base()
    ir["nodes"][0]["lines"][0]["speaker"] = "ghost"
    errs = crossref_errors(ir)
    assert any("ghost" in e and "character" in e for e in errs)


def test_null_speaker_is_narration_ok():
    ir = _base()
    ir["nodes"][0]["lines"][0]["speaker"] = None
    assert crossref_errors(ir) == []


def test_bad_talk_node():
    ir = _base()
    ir["places"][0]["interactables"][0]["action"]["node"] = "nope"
    assert any("nope" in e and "node" in e for e in crossref_errors(ir))


def test_bad_jump_and_menu_targets():
    ir = _base()
    ir["nodes"][0]["end"] = {"type": "jump", "target": "missing"}
    assert any("missing" in e and "node" in e for e in crossref_errors(ir))

    ir2 = _base()
    ir2["nodes"][0]["end"] = {"type": "menu", "choices": [
        {"text": "go", "target": "phantom"}]}
    assert any("phantom" in e and "node" in e for e in crossref_errors(ir2))


def test_bad_move_target():
    ir = _base()
    ir["places"][0]["interactables"][0]["action"] = {"type": "move", "target": "void"}
    assert any("void" in e and "place" in e for e in crossref_errors(ir))


def test_bad_item_in_take_and_condition():
    ir = _base()
    ir["places"][0]["interactables"][0]["action"] = {"type": "take", "item": "ghost_item"}
    assert any("ghost_item" in e and "item" in e for e in crossref_errors(ir))

    ir2 = _base()
    ir2["goal"] = {"item": "phantom_item"}
    assert any("phantom_item" in e and "item" in e for e in crossref_errors(ir2))


def test_bad_flag_in_condition_and_effect():
    ir = _base()
    ir["goal"] = {"flag": "unknown_flag"}
    assert any("unknown_flag" in e and "flag" in e for e in crossref_errors(ir))

    ir2 = _base()
    ir2["nodes"][0]["lines"][0]["effects"] = [{"set_flag": "phantom_flag"}]
    assert any("phantom_flag" in e and "flag" in e for e in crossref_errors(ir2))


def test_bad_var_in_condition_operand_and_effect():
    # var on the left of a comparison
    ir = _base()
    ir["goal"] = {"var": "missing_var", "op": ">", "value": 1}
    assert any("missing_var" in e and "variable" in e for e in crossref_errors(ir))

    # var-vs-var: the operand on the right
    ir2 = _base()
    ir2["goal"] = {"var": "trust", "op": ">", "value": {"var": "ghost_var"}}
    assert any("ghost_var" in e and "variable" in e for e in crossref_errors(ir2))

    # var inside an effect
    ir3 = _base()
    ir3["nodes"][0]["lines"][0]["effects"] = [{"add_var": {"var": "nope_var", "delta": 1}}]
    assert any("nope_var" in e and "variable" in e for e in crossref_errors(ir3))


def test_use_clauses_and_fallback_refs():
    ir = _base()
    ir["places"][0]["interactables"][0]["action"] = {
        "type": "use",
        "clauses": [{"requires": {"item": "no_item"},
                     "outcome": {"effects": [{"set_flag": "no_flag"}]}}],
        "fallback": {"effects": [{"add_var": {"var": "no_var", "delta": -1}}]},
    }
    errs = crossref_errors(ir)
    assert any("no_item" in e for e in errs)
    assert any("no_flag" in e for e in errs)
    assert any("no_var" in e for e in errs)


def test_bad_start_refs():
    ir = _base()
    ir["start"] = {"place": "nowhere"}
    assert any("start.place" in e and "nowhere" in e for e in crossref_errors(ir))

    ir2 = _base()
    ir2["start"] = {"node": "no_node"}
    assert any("start.node" in e and "no_node" in e for e in crossref_errors(ir2))


def test_nested_composite_condition():
    ir = _base()
    ir["goal"] = {"all": [{"flag": "door_open"},
                          {"any": [{"item": "item_key"}, {"flag": "ghost_flag"}]}]}
    errs = crossref_errors(ir)
    assert len(errs) == 1 and "ghost_flag" in errs[0]


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


def test_bad_combatant_character_and_stat_and_ability():
    ir = _combat()
    ir["combatants"][0]["character"] = "nobody"
    ir["combatants"][0]["stats"][0]["stat"] = "no_stat"
    ir["combatants"][0]["abilities"] = ["no_ability"]
    errs = crossref_errors(ir)
    assert any("nobody" in e and "character" in e for e in errs)
    assert any("no_stat" in e and "stat" in e for e in errs)
    assert any("no_ability" in e and "ability" in e for e in errs)


def test_bad_ability_cost_and_effect_and_scaling():
    ir = _combat()
    ir["abilities"][0]["cost"] = [{"stat": "ghost_stat", "amount": 2}]
    ir["abilities"][0]["effects"][0]["stat"] = "phantom_stat"
    ir["abilities"][0]["effects"][0]["formula"]["scales_with"] = "no_mod"
    errs = crossref_errors(ir)
    assert any("ghost_stat" in e for e in errs)
    assert any("phantom_stat" in e for e in errs)
    assert any("no_mod" in e for e in errs)


def test_bad_status_in_effect_and_world_bridge():
    ir = _combat()
    ir["abilities"][0]["effects"] = [
        {"status": "no_status", "duration": 2},
        {"world": {"set_flag": "no_flag"}},
    ]
    errs = crossref_errors(ir)
    assert any("no_status" in e and "status" in e for e in errs)
    assert any("no_flag" in e and "flag" in e for e in errs)


def test_bad_status_tick_stat():
    ir = _combat()
    ir["statuses"][0]["tick"][0]["stat"] = "ghost_hp"
    assert any("ghost_hp" in e and "stat" in e for e in crossref_errors(ir))


def test_bad_encounter_ref_and_condition_and_resolution():
    ir = _combat()
    ir["encounters"][0]["combatants"][0]["ref"] = "no_combatant"
    ir["encounters"][0]["defeat"] = {"when": {"flag": "ghost_flag"}}
    ir["encounters"][0]["on_victory"] = {"type": "jump", "target": "no_node"}
    errs = crossref_errors(ir)
    assert any("no_combatant" in e and "combatant" in e for e in errs)
    assert any("ghost_flag" in e and "flag" in e for e in errs)
    assert any("no_node" in e and "node" in e for e in errs)


def test_bad_start_combat_encounter_ref():
    ir = _combat()
    ir["places"][0]["interactables"][0]["action"]["encounter"] = "no_enc"
    assert any("no_enc" in e and "encounter" in e for e in crossref_errors(ir))
