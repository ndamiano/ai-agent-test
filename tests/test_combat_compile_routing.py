import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.modules import checks
from maestro.modules.combat import encounter_write_error

_COMBAT = {"combatants": [{"id": "cb_hero"}, {"id": "cb_foe"}]}


def _enc(position):
    hero = {"ref": "cb_hero", "faction": "player"}
    if position is not None:
        hero["position"] = position
    return {"id": "enc_1",
            "combatants": [hero, {"ref": "cb_foe", "faction": "enemy"}],
            "victory": {"all_defeated": "enemy"},
            "on_victory": {"type": "end", "ending": "win"}}


def test_encounter_write_rejects_flat_position():
    # the exact live failure: position written as a flat {x,y} instead of {cell:{x,y}}
    err = encounter_write_error(_enc({"x": 1, "y": 2}), _COMBAT)
    assert err and "position" in err and "cell" in err


def test_encounter_write_accepts_cell_or_no_position():
    assert encounter_write_error(_enc({"cell": {"x": 1, "y": 2}}), _COMBAT) is None
    assert encounter_write_error(_enc(None), _COMBAT) is None


def test_compile_slice_attributes_to_the_owning_module():
    # the routing key: a combat-slice compile error must resolve to a combat slice, not [places]
    assert checks.compile_slice("encounters/0/combatants/0/position: bad") == "encounters"
    assert checks.compile_slice("combatants/1/stats: bad") == "combatants"
    assert checks.compile_slice("places[2].interactables: bad") == "places"
    assert checks.compile_slice("nodes/0/lines/1/speaker: bad") == "nodes"
