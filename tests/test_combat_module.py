"""combat module: routing to Godot, the v_combat structural validator, and get_errors detection
(build target, encounter reachability, and combat-slice crossref attribution)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.modules import resolve_modules
from maestro.modules.context import Context
from maestro.modules.combat import v_combat, MODULE as COMBAT
from maestro.modules.module import ErrorType
from maestro.ir_crossref import slice_token


def _combat(**over):
    c = {
        "combat_model": "turn_based",
        "stats": [{"id": "hp", "default": 30, "min": 0, "max": 30, "role": "resource_depletable"},
                  {"id": "atk", "default": 5, "role": "modifier"}],
        "abilities": [{"id": "slash", "name": "Slash",
                       "targeting": {"shape": "single", "faction": "enemy", "range": "melee"},
                       "effects": [{"stat": "hp", "op": "damage", "formula": {"base": 4}}]}],
        "combatants": [{"id": "cb_hero", "character": "hero", "stats": [{"stat": "hp", "value": 30}],
                        "abilities": ["slash"]},
                       {"id": "cb_foe", "character": "foe", "stats": [{"stat": "hp", "value": 12}],
                        "abilities": ["slash"]}],
        "encounters": [{"id": "enc_1", "background": "bg_arena",
                        "combatants": [{"ref": "cb_hero", "faction": "player"},
                                       {"ref": "cb_foe", "faction": "enemy"}],
                        "victory": {"all_defeated": "enemy"},
                        "on_victory": {"type": "end", "ending": "win"}}],
    }
    c.update(over)
    return c


def _art(combat, *, reachable=True, hero=True, foe=True):
    chars = [{"id": "hero", "name": "Hero"}] if hero else []
    if foe:
        chars.append({"id": "foe", "name": "Foe"})
    hotspot = ({"id": "h1", "label": "Foe", "position": {"cell": {"x": 1, "y": 1}},
                "action": {"type": "start_combat", "encounter": "enc_1"}}
               if reachable else
               {"id": "h1", "label": "x", "position": {"cell": {"x": 1, "y": 1}},
                "action": {"type": "examine", "text": "nothing"}})
    return {
        "characters": {"characters": chars},
        "asset_manifest": {"backgrounds": [{"id": "bg_arena", "image_file": "a.png"}]},
        "nodes": {"node_ids": [], "nodes": {}},
        "places": {"start_place": "room", "place_ids": ["room"], "places": {
            "room": {"kind": "interior", "background": "bg_arena", "interactables": [hotspot]}}},
        "combat": combat,
    }


def _ctx(art, params=None):
    class S:
        run_dir = "/tmp/none"
        def load_artifact(self): return art
        def read_story_state(self): return {}
        def read_scratchpad(self): return {}
        def read_waivers(self): return []
        def read_human_todos(self): return []
    return Context(spec={"params": params or {}}, state=S(), artifact=art)


# ── routing ───────────────────────────────────────────────────────────────────
def test_combat_routes_to_godot():
    mods, engine = resolve_modules(["combat"])
    assert engine == "godot"
    assert {"combat", "world", "scenes", "cast"} <= set(mods)


# ── v_combat structural validator ──────────────────────────────────────────────
def test_v_combat_accepts_valid():
    assert v_combat(_combat()) is None


def test_v_combat_requires_a_depletable_stat():
    err = v_combat(_combat(stats=[{"id": "atk", "default": 5, "role": "modifier"}]))
    assert err and "resource_depletable" in err


def test_v_combat_rejects_undeclared_ability_ref():
    bad = _combat()
    bad["combatants"][0]["abilities"] = ["nonexistent"]
    assert "nonexistent" in v_combat(bad)


def test_v_combat_rejects_undeclared_effect_stat():
    bad = _combat()
    bad["abilities"][0]["effects"] = [{"stat": "ghost", "op": "damage", "formula": {"base": 1}}]
    assert "ghost" in v_combat(bad)


def test_v_combat_requires_two_factions():
    bad = _combat()
    bad["encounters"][0]["combatants"] = [{"ref": "cb_hero", "faction": "player"}]
    assert "fight" in v_combat(bad)


# ── get_errors detection ────────────────────────────────────────────────────────
def test_build_combat_fires_when_no_encounters():
    art = _art(_combat(encounters=[]))
    errs = COMBAT.get_errors(_ctx(art))
    assert any(e.code == "build_combat" and e.type is ErrorType.BUILD for e in errs)


def test_clean_combat_has_no_errors():
    assert COMBAT.get_errors(_ctx(_art(_combat()))) == []


def test_unreachable_encounter_detected():
    errs = COMBAT.get_errors(_ctx(_art(_combat(), reachable=False)))
    assert any(e.code == "encounters_reachable" and e.path == "enc_1" for e in errs)


def test_build_combat_prompt_gets_token_headroom():
    # the whole-block one-shot needs a bigger output ceiling than the small fix steps.
    build_err = next(e for e in COMBAT.get_errors(_ctx(_art(_combat(encounters=[]))))
                     if e.code == "build_combat")
    assert COMBAT.get_correction_prompt(_ctx(_art(_combat(encounters=[]))), build_err).max_tokens == 16000


def test_fix_combat_prompt_keeps_the_default_ceiling():
    fix_err = next(e for e in COMBAT.get_errors(_ctx(_art(_combat(), reachable=False)))
                   if e.code == "encounters_reachable")
    assert COMBAT.get_correction_prompt(_ctx(_art(_combat(), reachable=False)), fix_err).max_tokens is None


def test_dangling_character_routes_to_combat_as_crossref():
    # foe is referenced by cb_foe.character but not declared in cast -> a combat-slice crossref.
    errs = COMBAT.get_errors(_ctx(_art(_combat(), foe=False)))
    cross = [e for e in errs if e.code == "crossref"]
    assert cross and all(e.component == "combat" for e in cross)
    # and that path's slice is one world's terminal skips (so world never claims it).
    assert all(slice_token(e.path) in ("stats", "statuses", "abilities", "combatants", "encounters")
               for e in cross)
