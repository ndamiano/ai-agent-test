"""combat module: routing to Godot, the v_combat structural validator, and get_errors detection
(build target, encounter reachability, and combat-slice crossref attribution)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.modules import resolve_modules
from maestro.modules.context import Context
from maestro.modules.combat import v_combat, _encounter_error, _ENCOUNTER_TOOLS, MODULE as COMBAT
from maestro.modules.module import ErrorType
from maestro.ir_crossref import slice_token
from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools


def _combat_tools(tmp_path):
    state = RunState(tmp_path)
    return build_tools(Spec({"title": "T", "frozen": True, "modules": ["combat"], "params": {}}),
                       state), state


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


def test_v_combat_never_crashes_on_mistyped_ids():
    """A flailing small model nests an object where an id STRING belongs. v_combat must return an
    actionable message, never a TypeError on `<dict> in <set>` (the live-build crash)."""
    cases = [
        # status as an object (the exact crash from the build log)
        _combat(statuses=[{"id": "stun", "name": "Stun",
                           "tick": [{"status": {"id": "stun"}, "duration": 2}]}]),
        # stat-effect stat as an object
        _combat(abilities=[{"id": "a", "targeting": {"shape": "single", "faction": "enemy"},
                            "effects": [{"stat": {"id": "hp"}, "op": "damage"}]}]),
        # targeting shape as an object
        _combat(abilities=[{"id": "a", "targeting": {"shape": {"x": "single"}, "faction": "enemy"},
                            "effects": [{"stat": "hp", "op": "damage"}]}]),
        # cost stat as an object
        _combat(abilities=[{"id": "a", "targeting": {"shape": "single", "faction": "enemy"},
                            "cost": [{"stat": {"id": "mp"}, "amount": 1}],
                            "effects": [{"stat": "hp", "op": "damage"}]}]),
        # combatant ability as an object
        _combat(combatants=[{"id": "cb", "stats": [{"stat": "hp", "value": 1}],
                             "abilities": [{"id": "slash"}]}]),
        # encounter ref as an object
        _combat(encounters=[{"id": "e", "combatants": [{"ref": {"id": "cb_hero"}, "faction": "player"}],
                             "victory": {"all_defeated": "enemy"}}]),
        # formula as a string (the second live-build crash: 'str' has no attribute 'get')
        _combat(abilities=[{"id": "a", "targeting": {"shape": "single", "faction": "enemy"},
                            "effects": [{"stat": "hp", "op": "damage", "formula": "atk"}]}]),
    ]
    for c in cases:
        msg = v_combat(c)              # must not raise
        assert isinstance(msg, str) and msg, f"expected an error message for {c}"


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


def test_encounter_reports_all_issues_at_once():
    # A phantom ref AND a bare-string on_victory: both must surface in ONE message, or a small model
    # ping-pongs (fix ref, re-break on_victory, forever). This is the churn we hit live.
    e = {"id": "enc1",
         "combatants": [{"ref": "cb_hero", "faction": "player"},
                        {"ref": "cb_ghost", "faction": "enemy"}],
         "victory": {"all_defeated": "enemy"}, "on_victory": "end"}
    msg = _encounter_error(e, {"cb_hero"})
    assert "cb_ghost" in msg and "on_victory" in msg   # both, not one-at-a-time


def test_encounter_undeclared_ref_offers_authoring_path():
    # The fix for a missing enemy is to author it — the message must say write_combatant, and the
    # encounter phase must actually scope that tool, or the loop deadlocks.
    msg = _encounter_error({"id": "e", "combatants": [{"ref": "cb_wraith", "faction": "enemy"}],
                            "victory": {"all_defeated": "enemy"}}, set())
    assert "write_combatant" in msg
    assert "write_combatant" in _ENCOUNTER_TOOLS


# ── get_errors detection (decomposed: staged count targets in dependency order) ───
def test_min_encounters_fires_when_no_encounters():
    art = _art(_combat(encounters=[]))
    errs = COMBAT.get_errors(_ctx(art))
    assert any(e.code == "min_encounters" and e.type is ErrorType.BUILD for e in errs)


def test_meta_target_fires_first_when_no_stats():
    # no stat system => the foundation target gates everything else (dependency order).
    art = _art(_combat(stats=[]))
    errs = COMBAT.get_errors(_ctx(art))
    assert [e.code for e in errs] == ["build_combat_meta"]


def test_targets_are_staged_one_at_a_time():
    # meta ok, but no abilities => only min_abilities surfaces (not combatants/encounters yet).
    art = _art(_combat(abilities=[], combatants=[], encounters=[]))
    errs = COMBAT.get_errors(_ctx(art))
    assert [e.code for e in errs] == ["min_abilities"]


def test_encounter_on_victory_must_be_a_node_end_not_a_bare_string():
    # the live-build deadlock: a bare node-id string for on_victory passed write_encounter and only
    # blew up at compile (where WORLD owns the error and can't fix a combat slice). Catch it here.
    bad = _combat()
    bad["encounters"][0]["on_victory"] = "beat_entrance"
    msg = v_combat(bad)
    assert msg and "node_end" in msg and "on_victory" in msg
    # the valid jump shape passes
    bad["encounters"][0]["on_victory"] = {"type": "jump", "target": "beat_entrance"}
    assert v_combat(bad) is None


def test_clean_combat_has_no_errors():
    assert COMBAT.get_errors(_ctx(_art(_combat()))) == []


def test_unreachable_encounter_detected():
    errs = COMBAT.get_errors(_ctx(_art(_combat(), reachable=False)))
    assert any(e.code == "encounters_reachable" and e.path == "enc_1" for e in errs)


def test_count_target_declares_its_slice_guard():
    # Each count target is slot-guarded: its check declares the create tool + id keys, and the base
    # single fix installs that guard so a step can only ADD the next slice item.
    assert COMBAT._check_for("min_abilities").guard["count_tool"] == "write_ability"
    assert COMBAT._check_for("min_combatants").guard["count_tool"] == "write_combatant"
    assert COMBAT._check_for("min_encounters").guard["count_tool"] == "write_encounter"


def test_target_selects_its_own_skeleton():
    from maestro.modules.combat import SKEL_COMBATANT, SKEL_ABILITY
    err = next(e for e in COMBAT.get_errors(_ctx(_art(_combat(combatants=[], encounters=[]))))
               if e.code == "min_combatants")
    cp = COMBAT.get_correction_prompt(_ctx(_art(_combat(combatants=[]))), err)
    assert SKEL_COMBATANT in cp.system and SKEL_ABILITY not in cp.system


# ── slice tools: incremental, id-merged, validated against declared upstream ──────
def test_slice_tools_build_combat_incrementally(tmp_path):
    tools, state = _combat_tools(tmp_path)
    assert tools["set_combat_meta"](combat_model="turn_based",
        stats=[{"id": "hp", "default": 20, "min": 0, "max": 20, "role": "resource_depletable"}])["ok"]
    assert tools["write_ability"]("slash", {"name": "Slash",
        "targeting": {"shape": "single", "faction": "enemy"},
        "effects": [{"stat": "hp", "op": "damage", "formula": {"base": 4}}]})["ok"]
    assert tools["write_combatant"]("cb_hero", {"stats": [{"stat": "hp", "value": 20}],
                                                "abilities": ["slash"]})["ok"]
    assert tools["write_combatant"]("cb_foe", {"stats": [{"stat": "hp", "value": 8}],
                                               "abilities": ["slash"]})["ok"]
    assert tools["write_encounter"]("enc1", {"combatants": [
        {"ref": "cb_hero", "faction": "player"}, {"ref": "cb_foe", "faction": "enemy"}],
        "victory": {"all_defeated": "enemy"}})["ok"]
    combat = state.read_component("combat")
    assert [a["id"] for a in combat["abilities"]] == ["slash"]
    assert {c["id"] for c in combat["combatants"]} == {"cb_hero", "cb_foe"}
    assert v_combat(combat) is None


def test_ability_before_stats_is_rejected(tmp_path):
    tools, _ = _combat_tools(tmp_path)
    res = tools["write_ability"]("slash", {"targeting": {"shape": "single", "faction": "enemy"},
                                           "effects": [{"stat": "hp", "op": "damage"}]})
    assert not res["ok"] and "stat system first" in res["error"]


def test_slice_tool_validates_against_declared_ids(tmp_path):
    tools, _ = _combat_tools(tmp_path)
    tools["set_combat_meta"](stats=[{"id": "hp", "default": 9, "role": "resource_depletable"}])
    # ability referencing an undeclared stat
    bad = tools["write_ability"]("x", {"targeting": {"shape": "single", "faction": "enemy"},
                                       "effects": [{"stat": "mp", "op": "damage"}]})
    assert not bad["ok"] and "mp" in bad["error"]
    # combatant referencing an ability that doesn't exist yet
    bad2 = tools["write_combatant"]("cb", {"stats": [{"stat": "hp", "value": 9}],
                                           "abilities": ["nope"]})
    assert not bad2["ok"] and "nope" in bad2["error"]


def test_write_ability_rejects_non_string_requires_ids(tmp_path):
    # observed: a model wrote a whole dict as requires.var (stat-as-condition proxy), which
    # crashed the crossref walk downstream — the write gate must catch it with a message
    tools, _ = _combat_tools(tmp_path)
    tools["set_combat_meta"](stats=[{"id": "hp", "default": 9, "role": "resource_depletable"}])
    bad = tools["write_ability"]("burst", {
        "targeting": {"shape": "single", "faction": "enemy"},
        "effects": [{"stat": "hp", "op": "damage"}],
        "requires": {"var": {"stat_ref": "hp"}, "op": ">=", "value": 15}})
    assert not bad["ok"] and "string id" in bad["error"] and "requires" in bad["error"]
    ok = tools["write_ability"]("burst", {
        "targeting": {"shape": "single", "faction": "enemy"},
        "effects": [{"stat": "hp", "op": "damage"}],
        "requires": {"flag": "enraged"}})
    assert ok["ok"], ok.get("error")


def test_crossref_reports_non_string_cond_id_instead_of_crashing():
    from maestro.ir_crossref import crossref_records
    ir = {"meta": {"title": "t"}, "characters": [], "backgrounds": [],
          "abilities": [{"id": "burst", "requires": {"var": {"bad": 1}, "op": ">=", "value": 5},
                         "targeting": {"shape": "single", "faction": "enemy"},
                         "effects": [{"stat": "hp", "op": "damage"}]}]}
    recs = crossref_records(ir)
    hits = [r for r in recs if "must be a string id" in r["message"]]
    assert hits and "abilities[burst].requires.var" in hits[0]["path"]


def test_set_combat_meta_rejects_no_depletable_stat(tmp_path):
    tools, _ = _combat_tools(tmp_path)
    res = tools["set_combat_meta"](stats=[{"id": "atk", "default": 5, "role": "modifier"}])
    assert not res["ok"] and "resource_depletable" in res["error"]


def test_write_ability_id_merge_replaces_not_dupes(tmp_path):
    tools, state = _combat_tools(tmp_path)
    tools["set_combat_meta"](stats=[{"id": "hp", "default": 9, "role": "resource_depletable"}])
    tools["write_ability"]("slash", {"name": "Slash", "targeting": {"shape": "single", "faction": "enemy"},
                                     "effects": [{"stat": "hp", "op": "damage", "formula": {"base": 3}}]})
    tools["write_ability"]("slash", {"name": "Slash II", "targeting": {"shape": "single", "faction": "enemy"},
                                     "effects": [{"stat": "hp", "op": "damage", "formula": {"base": 9}}]})
    abilities = state.read_component("combat")["abilities"]
    assert len(abilities) == 1 and abilities[0]["name"] == "Slash II"


class _StubConn:
    """Always answers with the SAME tool call (a tool the active step did NOT offer)."""
    def __init__(self, name, args):
        import json as _json
        self._tc = [{"id": "1", "function": {"name": name, "arguments": _json.dumps(args)}}]

    def generate_with_tools(self, messages, schemas, **kw):
        return {"choices": [{"message": {"content": None, "tool_calls": self._tc}}]}


def test_off_scope_tool_is_refused_by_dispatch(tmp_path):
    # The thrash fix: a small model calls a tool named in the prose but NOT offered this step
    # (write_combatant while the target is "author one ability"). dispatch must refuse it.
    from maestro.services import Services, BudgetExhausted
    from maestro.modules.context import build_context
    state = RunState(tmp_path)
    spec = Spec({"title": "T", "frozen": True, "modules": ["combat"],
                 "params": {"min_abilities": 1, "min_combatants": 1, "min_encounters": 1}})
    state.write_component("combat", {"combat_model": "turn_based",
        "stats": [{"id": "hp", "default": 9, "role": "resource_depletable"}]})
    tools = build_tools(spec, state)
    ctx = build_context(spec.data, state)
    err = next(e for e in COMBAT.get_errors(ctx) if e.code == "min_abilities")

    conn = _StubConn("write_combatant", {"combatant_id": "cb_x",
                                         "content": {"stats": [{"stat": "hp", "value": 5}], "abilities": []}})
    services = Services(conn, tools, spec.data, state, budget=3)
    try:
        COMBAT.get_fix(ctx, err)(services)
    except BudgetExhausted:
        pass
    # write_combatant was not in this target's offered set, so it must never have run.
    assert not (state.read_component("combat") or {}).get("combatants")


def test_slice_skeletons_list_live_declared_ids(tmp_path):
    # The skeleton's example ids ("hp"/"slash") are what a small model copies — every slice
    # skeleton must end with the ACTUAL declared ids (7 rejected writes in the live build).
    from maestro.modules.context import build_context
    state = RunState(tmp_path)
    state.write_component("characters", {"characters": [{"id": "sister_elara", "name": "E"}]})
    state.write_component("combat", {"combat_model": "turn_based",
        "stats": [{"id": "faith", "default": 9, "role": "resource_depletable"}],
        "abilities": [{"id": "warden_bane", "targeting": {"shape": "single", "faction": "enemy"},
                       "effects": [{"stat": "faith", "op": "damage", "formula": {"base": 2}}]}]})
    spec = Spec({"title": "T", "frozen": True, "modules": ["combat"],
                 "params": {"min_abilities": 2, "min_combatants": 1, "min_encounters": 1}})
    ctx = build_context(spec.data, state)
    ab = next(e for e in COMBAT.get_errors(ctx) if e.code == "min_abilities")
    p = COMBAT.get_correction_prompt(ctx, ab)
    assert "THE DECLARED IDS" in p.system and "faith" in p.system

    ctx2 = build_context(Spec({"title": "T", "frozen": True, "modules": ["combat"],
                               "params": {"min_abilities": 1, "min_combatants": 1,
                                          "min_encounters": 1}}).data, state)
    cb = next(e for e in COMBAT.get_errors(ctx2) if e.code == "min_combatants")
    p2 = COMBAT.get_correction_prompt(ctx2, cb)
    assert "warden_bane" in p2.system and "sister_elara" in p2.system


def test_dangling_character_routes_to_combat_as_crossref():
    # foe is referenced by cb_foe.character but not declared in cast -> a combat-slice crossref.
    errs = COMBAT.get_errors(_ctx(_art(_combat(), foe=False)))
    cross = [e for e in errs if e.code == "crossref"]
    assert cross and all(e.component == "combat" for e in cross)
    # and that path's slice is one world's terminal skips (so world never claims it).
    assert all(slice_token(e.path) in ("stats", "statuses", "abilities", "combatants", "encounters")
               for e in cross)
