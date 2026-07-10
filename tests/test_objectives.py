"""objectives — the goal state machine over the flag substrate (W2).

Pins the contract: the archetype step templates (code owns transition semantics — the model fills
slots), the demand-from-tensions fan, the path-aware producer-before-consumer ordering check (the
parked wiring check made real over views.reachable/shortest_path), journal completeness, the IR
lift (unlike bible — the runtime renders the journal; provenance stripped), and the godot-only
routing. The hand-sketched objectives from docs/examples/world_game_notes.md (adapted to the
landed schema) must validate against the gold game — that is the load-bearing expressibility claim.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from conftest import load_example, make_ctx, seed_frozen_run
from maestro.ir_assemble import assemble_ir
from maestro.ir_crossref import crossref_errors
from maestro.modules import compose
from maestro.modules.module import (MODULE_REGISTRY, ErrorType, selectable_catalog,
                                    unprojectable)
from maestro.modules import objectives as O
from maestro.tools import build_tools

OBJ = MODULE_REGISTRY["objectives"]
_RUNTIME = Path(__file__).parent.parent / "src" / "godot" / "runtime"
_SPEC = {"params": {}, "modules": ["bible", "objectives"]}


def _bible():
    return {"setting": "a drought-starved river barony",
            "factions": [{"id": "fac_guild", "name": "Guild", "wants": "the gate open"},
                         {"id": "fac_keep", "name": "Keep", "wants": "the levy paid"}],
            "tensions": [
                {"id": "tension_water", "summary": "the weir is drying the town",
                 "between": ["fac_guild", "fac_keep"], "scale": "main"},
                {"id": "tension_mine", "summary": "the mine kills prospectors",
                 "between": ["fac_keep"], "scale": "side"},
                {"id": "tension_faith", "summary": "the priests blame the guild",
                 "between": ["fac_guild"], "scale": "side"}]}


def _fetch_obj(**over):
    o = {"id": "obj_weir", "tension": "tension_water", "archetype": "fetch",
         "title": "The Low Water", "main": True,
         "steps": [
             {"id": "s1", "summary": "hear the guild's case", "advance": {"flag": "weir_heard"}},
             {"id": "s2", "summary": "recover the crank", "advance": {"item": "sluice_crank"}},
             {"id": "s3", "summary": "throw the sluice",
              "resolutions": [{"id": "opened", "flag": "weir_open"}]}],
         "journal": {"offered": "o", "s1": "a", "s2": "b", "s3": "c", "resolved.opened": "r"}}
    o.update(over)
    return o


# ── archetype validation: code owns the shape, the model fills slots ──────────
def test_v_objective_accepts_the_valid_fetch():
    assert O.v_objective_one(_fetch_obj()) is None


def _mut(fn):
    o = _fetch_obj()
    fn(o)
    return o


@pytest.mark.parametrize("mutate, needle", [
    # an archetype outside the library — the model may not invent quest shapes
    (lambda o: o.__setitem__("archetype", "heist"), "not in the library"),
    # below the archetype's step floor
    (lambda o: o.__setitem__("steps", o["steps"][-1:]), "at least 2 steps"),
    # a NON-final step carrying resolutions — only the chain's last step resolves
    (lambda o: o["steps"][0].__setitem__("resolutions", [{"id": "x", "flag": "f"}]),
     "only the FINAL step"),
    # the final step carrying advance — its completion IS the resolution
    (lambda o: o["steps"][-1].__setitem__("advance", {"flag": "x"}), "never 'advance'"),
    # advance must be a real condition from the shared grammar
    (lambda o: o["steps"][0].__setitem__("advance", {}), "condition"),
    (lambda o: o["steps"][0].__setitem__("advance", {"flags": "weir_heard"}), "invalid condition"),
    # duplicate step ids break the chain's identity
    (lambda o: o["steps"][1].__setitem__("id", "s1"), "duplicate step id"),
    # a resolution without a flag has nothing for the world to check afterwards
    (lambda o: o["steps"][-1]["resolutions"][0].pop("flag"), "needs a 'flag'"),
    # journal must carry text per state (incl. per-resolution)
    (lambda o: o["journal"].pop("resolved.opened"), "resolved.opened"),
    (lambda o: o["journal"].__setitem__("s1", "  "), "s1"),
    # W3 fields (giver/rewards) are not in the W2 shape — reject junk keys loudly
    (lambda o: o.__setitem__("giver", "hessa"), "does not take"),
    (lambda o: o["steps"][0].__setitem__("advance_flag", "weir_heard"), "does not take"),
], ids=["unknown_archetype", "too_few_steps", "mid_chain_resolutions", "final_with_advance",
        "empty_advance", "malformed_advance", "dup_step_ids", "flagless_resolution",
        "missing_resolution_journal", "blank_step_journal", "unknown_objective_key",
        "sketch_advance_flag_key"])
def test_v_objective_rejects_malformations(mutate, needle):
    err = O.v_objective_one(_mut(mutate))
    assert err and needle in err


@pytest.mark.parametrize("archetype, n_res, ok", [
    ("fetch", 1, True), ("fetch", 2, False),          # single-outcome archetypes
    ("investigate", 1, True), ("escort", 1, True),
    ("moral_fork", 2, True), ("moral_fork", 1, False), ("moral_fork", 3, False),  # exactly two
    ("broker", 2, True), ("broker", 3, True), ("broker", 1, False),               # pick a side
])
def test_archetype_resolution_counts_enforced(archetype, n_res, ok):
    # broker's floor is 3 steps, so give every variant enough advance steps to isolate the
    # resolution-count rule from the step floor.
    res = [{"id": f"r{k}", "flag": f"f{k}"} for k in range(n_res)]
    steps = [{"id": "s1", "summary": "a", "advance": {"flag": "x"}},
             {"id": "s2", "summary": "b", "advance": {"flag": "y"}},
             {"id": "s3", "summary": "c", "resolutions": res}]
    journal = {"offered": "o", "s1": "a", "s2": "b",
               **{f"resolved.r{k}": "t" for k in range(n_res)}}
    o = _fetch_obj(archetype=archetype, steps=steps, journal=journal)
    assert (O.v_objective_one(o) is None) is ok


def test_v_objectives_requires_exactly_one_main_but_allows_shared_tensions():
    # The gold game hangs THREE quests off one tension web — same tension twice must be legal;
    # two mains (two win paths) must not.
    a, b = _fetch_obj(), _fetch_obj(id="obj_two", main=False)
    assert O.v_objectives({"objectives": [a, b]}) is None
    b2 = _fetch_obj(id="obj_two", main=True)
    assert "exactly one main" in O.v_objectives({"objectives": [a, b2]})


# ── demand-from-tensions (inventory-shaped, keyed on the tension id) ──────────
def test_demand_fans_one_objective_per_uncovered_tension_main_first():
    art = {"bible": _bible()}
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "demanded_objectives"]
    assert [e.ref for e in errs] == ["tension_water", "tension_mine", "tension_faith"]
    assert all(e.type is ErrorType.BUILD and e.kind == "tension" for e in errs)
    assert "MAIN tension" in errs[0].message
    # covering one shrinks the owed set (visible progress), keyed on the REAL tension id
    art["objectives"] = {"objectives": [_fetch_obj()]}
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "demanded_objectives"]
    assert [e.ref for e in errs] == ["tension_mine", "tension_faith"]
    chk = OBJ._check_for("demanded_objectives")
    assert chk.when_clean is True
    assert chk.guard["count_tool"] == "add_objective" and chk.guard["cap"](None) == 1


def test_guard_prepare_code_fills_id_and_tension():
    # The code-filled-id prepare pattern (story._fill_beat_id / scenes._stamp_node): the assigned
    # slot decides the objective id AND the tension — a model-picked id/tension is discarded.
    view = O.objectives_view({"bible": _bible()})
    assert [t["id"] for t in view["open_tensions"]][0] == "tension_water"   # main first
    assigned = O._pick_tension(view, 0)
    assert assigned == {"id": "obj_water", "tension": "tension_water"}
    args = O._stamp_objective(view, assigned,
                              {"objective_id": "obj_wrong", "content": {"tension": "tension_junk"}})
    assert args["objective_id"] == "obj_water"
    assert args["content"]["tension"] == "tension_water"


# ── exactly-one-main: derived from the bible, repaired deterministically ──────
class _StubServices:
    allowed = "sentinel"
    def _report(self, s): pass


def test_one_main_detects_and_restamps_from_the_bible():
    objs = [_fetch_obj(main=False), _fetch_obj(id="obj_mine", tension="tension_mine", main=True)]
    art = {"bible": _bible(), "objectives": {"objectives": objs}}
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "one_main_objective"]
    assert len(errs) == 1 and errs[0].type is ErrorType.FIX
    assert OBJ._check_for("one_main_objective").run is O._restamp_main

    written = {}
    O._restamp_main(OBJ, make_ctx(_SPEC, art), errs[0], 0, _StubServices(),
                    lambda name, a: written.update({name: a}) or {"ok": True})
    stamped = written["write_component"]["content"]["objectives"]
    # the FIRST objective citing the main tension is the win path; everything else is side
    assert [o["main"] for o in stamped] == [True, False]


def test_one_main_silent_when_flags_agree_or_main_objective_absent():
    agree = {"bible": _bible(), "objectives": {"objectives": [
        _fetch_obj(), _fetch_obj(id="obj_b", main=False)]}}
    assert not [e for e in OBJ.get_errors(make_ctx(_SPEC, agree))
                if e.code == "one_main_objective"]
    # no objective cites the main tension yet -> authoring (demand) owns it, not a restamp
    absent = {"bible": _bible(), "objectives": {"objectives": [
        _fetch_obj(id="obj_mine", tension="tension_mine", main=False)]}}
    assert not [e for e in OBJ.get_errors(make_ctx(_SPEC, absent))
                if e.code == "one_main_objective"]


# ── the path-aware ordering check (the parked wiring check, made real) ────────
def _wired_world(door_gate):
    """A two-room world: the giver scene produces 'heard'; 'door_open' is produced ONLY by a lever
    INSIDE room_b, whose entrance is gated on `door_gate`; 'finished' is produced back in room_a."""
    obj = {"id": "obj_door", "tension": "tension_water", "archetype": "fetch",
           "title": "The Door", "main": True,
           "steps": [
               {"id": "s1", "summary": "hear it", "advance": {"flag": "heard"}},
               {"id": "s2", "summary": "open the way", "advance": {"flag": "door_open"}},
               {"id": "s3", "summary": "finish it",
                "resolutions": [{"id": "done", "flag": "finished"}]}],
           "journal": {"offered": "o", "s1": "a", "s2": "b", "resolved.done": "r"}}
    return {
        "bible": _bible(),
        "nodes": {"node_ids": ["n_giver"], "nodes": {"n_giver": {
            "lines": [{"speaker": None, "text": "x", "effects": [{"set_flag": "heard"}]}],
            "end": {"type": "return"}}}},
        "places": {"place_ids": ["room_a", "room_b"], "start_place": "room_a",
                   "flags": ["heard", "door_open", "finished"],
                   "places": {
                       "room_a": {"kind": "room", "interactables": [
                           {"id": "h_talk", "action": {"type": "talk", "node": "n_giver"}},
                           {"id": "h_door", "action": {"type": "move", "target": "room_b",
                                                       "requires": door_gate}},
                           {"id": "h_final", "action": {"type": "use", "fallback": {
                               "text": "t", "effects": [{"set_flag": "finished"}]}}}]},
                       "room_b": {"kind": "room", "interactables": [
                           {"id": "h_lever", "action": {"type": "use", "fallback": {
                               "text": "t", "effects": [{"set_flag": "door_open"}]}}}]}}},
        "objectives": {"objectives": [obj]},
    }


def test_ordering_catches_a_producer_sitting_behind_its_own_gate():
    # room_b (the only producer of door_open) is entered through a gate on door_open ITSELF —
    # the inversion nothing but a path-aware walk can see. state.py's existence-only wiring is
    # blind to it: a producer and a consumer both exist.
    art = _wired_world({"flag": "door_open"})
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "producer_order"]
    assert len(errs) == 1
    assert errs[0].ref == "door_open" and errs[0].path == "obj_door.s2"
    assert "OUT OF ORDER" in errs[0].message
    assert "door_open" in errs[0].message   # names the offending gate via shortest_path


def test_wiring_fix_prompt_names_the_real_place_and_node_ids():
    # The wiring fixer edits OTHER components (edit_node / add_interactable), so its prompt must
    # carry the id-level indexes of what exists — run cec82b74665a burned ~100 steps guessing
    # 'place_inn'/'inn_talk_mara' because ctx_structural showed only the objectives digest.
    art = _wired_world({"flag": "heard"})
    art["objectives"]["objectives"][0]["steps"][1]["advance"] = {"flag": "ghost_flag"}
    ctx = make_ctx(_SPEC, art)
    err = [e for e in OBJ.get_errors(ctx) if e.code == "producer_order"][0]
    cp = OBJ.get_correction_prompt(ctx, err)
    assert "room_a" in cp.user and "room_b" in cp.user   # the places the fix can target
    assert "n_giver" in cp.user                          # the nodes it can edit
    # The produced-state catalog: repointing is only legal at state that exists — without the
    # list the model plays a shell game, repointing each phantom flag at another phantom
    # (run 8c19f94fed80: ledger_confronted -> ledger_read -> ...).
    assert "STATE THAT EXISTS" in cp.user
    assert "heard" in cp.user and "door_open" in cp.user  # flags with real producers, with sites
    assert "NO producer" in cp.user                       # the phantom-repoint warning


def test_ordering_catches_a_producer_behind_a_LATER_steps_gate():
    # the door is gated on 'finished' — a step-3 grant — so step 2's producer is order-inverted
    art = _wired_world({"flag": "finished"})
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "producer_order"]
    assert [e.ref for e in errs] == ["door_open"]


def test_ordering_clean_when_the_gate_uses_an_earlier_step():
    # gating room_b on 'heard' (step 1's grant) is the CORRECT order — the liberal rule opens
    # every edge not gated on this-or-later state, so proper chains never false-positive
    art = _wired_world({"flag": "heard"})
    assert not [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "producer_order"]


def test_missing_producer_is_its_own_error():
    art = _wired_world({"flag": "heard"})
    art["objectives"]["objectives"][0]["steps"][1]["advance"] = {"flag": "ghost_flag"}
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "producer_order"]
    assert len(errs) == 1 and errs[0].ref == "ghost_flag"
    assert "NOTHING produces it" in errs[0].message


def test_wiring_checks_wait_for_realization_content():
    # world-first authors the chains BEFORE places/scenes exist — an empty world must not read
    # as a wiring defect (the loop would thrash on fixes with nothing to edit)
    art = {"bible": _bible(), "objectives": {"objectives": [_fetch_obj()]}}
    codes = {e.code for e in OBJ.get_errors(make_ctx(_SPEC, art))}
    assert "producer_order" not in codes and "resolutions_reachable" not in codes


@pytest.mark.parametrize("break_it, needle", [
    # nothing sets the resolution flag at all — the ending can never happen
    (lambda a: a["places"]["places"]["room_a"]["interactables"].pop(2), "NOTHING sets it"),
    # the producer exists but is gated on the resolution's own flag — the degenerate inversion
    (lambda a: a["places"]["places"]["room_a"]["interactables"][2].__setitem__(
        "action", {"type": "use", "clauses": [{"requires": {"flag": "finished"},
                                               "outcome": {"text": "t", "effects": [
                                                   {"set_flag": "finished"}]}}]}),
     "unreachable"),
], ids=["no_producer", "self_gated_producer"])
def test_resolutions_reachable(break_it, needle):
    art = _wired_world({"flag": "heard"})
    break_it(art)
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "resolutions_reachable"]
    assert len(errs) == 1 and errs[0].ref == "finished" and needle in errs[0].message


# ── journal completeness (repair-side; add_objective is born-compliant) ───────
def test_journal_completeness_names_the_missing_states():
    art = _wired_world({"flag": "heard"})
    del art["objectives"]["objectives"][0]["journal"]["s1"]
    del art["objectives"]["objectives"][0]["journal"]["resolved.done"]
    errs = [e for e in OBJ.get_errors(make_ctx(_SPEC, art)) if e.code == "journal_complete"]
    assert len(errs) == 1 and errs[0].path == "obj_door"
    assert "'s1'" in errs[0].message and "'resolved.done'" in errs[0].message


def test_final_step_journal_entry_is_optional():
    # the sketch's obj_water has no entry for its resolution step — the last ADVANCE step's
    # entry already carries the guidance, so only advance steps + resolutions are required
    o = _fetch_obj()
    assert "s3" not in O.required_journal_keys(o)
    assert O.required_journal_keys(o) == {"offered", "s1", "s2", "resolved.opened"}


# ── slice tools: round-trip + write-time policy ───────────────────────────────
def _tools(tmp_path):
    state = seed_frozen_run(tmp_path, "g", modules=["bible", "objectives"])
    tools = build_tools(state.read_spec(), state, compose(["bible", "objectives"]))
    tools["set_bible"]("a barony", [{"id": "fac_a", "name": "A", "wants": "w"}])
    tools["add_tension"]("tension_water", {"summary": "s", "scale": "main", "between": ["fac_a"]})
    tools["add_tension"]("tension_mine", {"summary": "s", "scale": "side", "between": ["fac_a"]})
    return tools, state


def _content(**over):
    c = {k: v for k, v in _fetch_obj().items() if k not in ("id", "main")}
    c.update(over)
    return c


def test_add_objective_round_trip_stamps_main_from_the_tension(tmp_path):
    tools, state = _tools(tmp_path)
    r = tools["add_objective"]("obj_weir", _content())
    assert r["ok"] and r["main"] is True
    # a SECOND quest on the main tension is legal but not a second win path
    r2 = tools["add_objective"]("obj_more", _content(title="More"))
    assert r2["ok"] and r2["main"] is False
    saved = state.load_artifact()["objectives"]["objectives"]
    assert [o["id"] for o in saved] == ["obj_weir", "obj_more"]
    assert [o["main"] for o in saved] == [True, False]


@pytest.mark.parametrize("objective_id, content_over, needle", [
    ("obj_weir", {"tension": "tension_ghost"}, "not declared in the bible"),
    ("obj_weir", {"archetype": "heist"}, "not in the library"),
    ("obj_weir", {"journal": {"offered": "o"}}, "journal is missing"),   # born-compliant
], ids=["unknown_tension", "bad_archetype", "incomplete_journal"])
def test_add_objective_rejects_at_write_time(tmp_path, objective_id, content_over, needle):
    tools, _ = _tools(tmp_path)
    r = tools["add_objective"](objective_id, _content(**content_over))
    assert not r["ok"] and needle in r["error"]


def test_add_objective_refuses_duplicate_ids(tmp_path):
    tools, _ = _tools(tmp_path)
    assert tools["add_objective"]("obj_weir", _content())["ok"]
    r = tools["add_objective"]("obj_weir", _content())
    assert not r["ok"] and "already exists" in r["error"]


def test_edit_objective_step_patches_and_revalidates(tmp_path):
    tools, state = _tools(tmp_path)
    tools["add_objective"]("obj_weir", _content())
    # patch one step's advance + merge a journal entry in one repair call
    r = tools["edit_objective_step"]("obj_weir", step_id="s2",
                                     advance={"flag": "crank_taken"},
                                     journal={"s2": "new guidance"})
    assert r["ok"]
    o = state.load_artifact()["objectives"]["objectives"][0]
    assert o["steps"][1]["advance"] == {"flag": "crank_taken"}
    assert o["journal"]["s2"] == "new guidance" and o["journal"]["s1"] == "a"   # merge, not replace
    # a patch that breaks the archetype shape is rejected and NOT persisted
    bad = tools["edit_objective_step"]("obj_weir", step_id="s1",
                                       resolutions=[{"id": "x", "flag": "f"}])
    assert not bad["ok"] and "only the FINAL step" in bad["error"]
    assert "resolutions" not in state.load_artifact()["objectives"]["objectives"][0]["steps"][0]
    # unknown targets fail loudly
    assert not tools["edit_objective_step"]("obj_ghost", step_id="s1", summary="x")["ok"]
    assert not tools["edit_objective_step"]("obj_weir", step_id="s9", summary="x")["ok"]


# ── the IR lift (unlike bible) + crossref ─────────────────────────────────────
def _min_art():
    return {
        "characters": {"characters": [{"id": "al", "name": "Al"}]},
        "nodes": {"node_ids": ["n1"], "nodes": {"n1": {
            "lines": [{"speaker": "al", "text": "hi",
                       "effects": [{"set_flag": "weir_heard"}]}],
            "end": {"type": "end"}}}},
        "items": {"items": [{"id": "sluice_crank", "name": "Sluice Crank"}]},
        "bible": _bible(),
        "objectives": {"objectives": [_fetch_obj()]},
    }


def test_ir_lift_strips_provenance_and_stays_schema_valid():
    import jsonschema
    ir = assemble_ir(_min_art())
    assert len(ir["objectives"]) == 1
    lifted = ir["objectives"][0]
    # tension is a BIBLE id (authoring layer, never the IR); archetype is the authoring template
    assert "tension" not in lifted and "archetype" not in lifted
    assert lifted["main"] is True and lifted["journal"]["offered"] == "o"
    # the bible itself still never leaks
    assert "bible" not in ir and "tensions" not in ir
    # referenced flags are auto-declared, so the lifted conditions resolve
    assert {"weir_heard", "weir_open"} <= set(ir["flags"])
    schema = json.loads((Path(__file__).parent.parent / "docs" / "game_ir.schema.json")
                        .read_text())
    jsonschema.Draft202012Validator(
        {k: v for k, v in schema.items() if k != "examples"}).validate(ir)
    assert crossref_errors(ir) == []


def test_crossref_validates_objective_condition_refs():
    # variables are never auto-declared (a typo must be CAUGHT) — a var ref in an advance
    # condition crossrefs like any gate's
    art = _min_art()
    art["objectives"]["objectives"][0]["steps"][0]["advance"] = \
        {"var": "trust", "op": ">=", "value": 2}
    errs = crossref_errors(assemble_ir(art))
    assert len(errs) == 1 and "objectives[obj_weir].steps[s1].advance" in errs[0]


def test_objectives_module_owns_its_crossref_slice():
    # an objectives-slice failure is fixed via edit_objective_step — world/scenes must route it
    # here, not claim it on places/nodes (they cannot rewrite the objectives doc). crossref is
    # when_clean, so everything else (demand, wiring) must be satisfied for it to surface: one
    # covered tension, every ref produced in n1 — only the undeclared var remains.
    art = _min_art()
    art["bible"]["tensions"] = art["bible"]["tensions"][:1]
    art["nodes"]["nodes"]["n1"]["lines"][0]["effects"] = [
        {"set_flag": "weir_heard"}, {"add_item": "sluice_crank"}, {"set_flag": "weir_open"},
        {"set_var": {"var": "trust", "value": 2}}]
    art["objectives"]["objectives"][0]["steps"][0]["advance"] = \
        {"var": "trust", "op": ">=", "value": 2}
    spec = {"params": {}, "modules": ["bible", "objectives", "scenes"]}
    mine = [e for e in OBJ.get_errors(make_ctx(spec, art)) if e.code == "crossref"]
    assert len(mine) == 1 and mine[0].component == "objectives"
    scenes = MODULE_REGISTRY["scenes"]
    assert not [e for e in scenes.get_errors(make_ctx(spec, art))
                if e.code == "crossref" and "objectives[" in (e.path or "")]


def test_objective_item_refs_are_inventory_demand():
    # {"item": "sluice_crank"} in an advance is DEMAND like any gate — inventory authors the
    # item the quest names (the take-has-no-effects gap closes without a dead flag)
    from maestro.modules.inventory import demanded_items
    art = _min_art()
    del art["items"]
    assert "sluice_crank" in demanded_items(art)


def test_objective_conditions_count_as_state_consumers():
    # a flag whose only reader is the quest chain must not read as dead state ("use it or cut
    # it" would cut the chain's spine)
    from maestro.modules.state import state_wiring
    recs = state_wiring(_min_art())
    assert not any(r["ref"] == "weir_heard" and "never used" in r["message"] for r in recs)


# ── the gold game: the notes' sketch (adapted) validates against it ───────────
def _ir_to_artifact(ir):
    """docs/examples ships whole IR docs; the module checks walk COMPONENTS — re-shard the gold
    game into the on-disk component shapes (the inverse of assemble_ir, for the slices the
    objectives checks read)."""
    return {
        "characters": {"characters": ir.get("characters", [])},
        "nodes": {"node_ids": [n["id"] for n in ir["nodes"]],
                  "nodes": {n["id"]: {k: v for k, v in n.items() if k != "id"}
                            for n in ir["nodes"]}},
        "places": {"place_ids": [p["id"] for p in ir.get("places", [])],
                   "places": {p["id"]: {k: v for k, v in p.items() if k != "id"}
                              for p in ir.get("places", [])},
                   "start_place": ir.get("start", {}).get("place"),
                   "start_spawn": ir.get("start", {}).get("spawn"),
                   "flags": ir.get("flags", []),
                   "variables": ir.get("variables", []),
                   "goal": ir.get("goal")},
        "items": {"items": ir.get("items", [])},
        "combat": {k: ir[k] for k in ("combat_model", "stats", "statuses", "abilities",
                                      "combatants", "encounters", "progression") if k in ir},
    }


def _low_water_objectives():
    """The world_game_notes.md sketch, adapted to the landed schema: `advance` is a condition
    (obj_weir.s2 advances on HOLDING sluice_crank — the notes' has_crank gap), giver/rewards are
    W3 fields and dropped, journal keys unchanged. Same flags as the shipped IR — the load-bearing
    claim that objectives is a skin over the existing substrate."""
    return {"objectives": [
        {"id": "obj_weir", "tension": "tension_water", "archetype": "fetch",
         "title": "The Low Water", "main": True,
         "steps": [
             {"id": "s1", "summary": "hear the guild's case at the forge",
              "advance": {"flag": "weir_heard"}},
             {"id": "s2", "summary": "recover a crank that fits the sluice (the old mill)",
              "advance": {"item": "sluice_crank"}},
             {"id": "s3", "summary": "clear Sergeant Kel from the weir",
              "advance": {"flag": "kel_defeated"}},
             {"id": "s4", "summary": "throw the sluice and give the river back",
              "resolutions": [{"id": "opened", "flag": "weir_open"}]}],
         "journal": {
             "offered": "Hessa says the baron's weir is drying Vessle. She wants the gate opened.",
             "s1": "The guild won't march. Open the sluice yourself: you'll need a crank and a way past the guard.",
             "s2": "The mill's crank fits the weir. Garrow's squatting there — ask, don't crowd him.",
             "s3": "Sergeant Kel holds the sluice. Move him — words or blows.",
             "s4": "Crank in hand, Kel down. Throw the gate.",
             "resolved.opened": "The Vhel runs to Vessle again."}},
        {"id": "obj_water", "tension": "tension_water", "archetype": "investigate",
         "title": "Bad Water", "main": False,
         "steps": [
             {"id": "s1", "summary": "hear the well-keeper", "advance": {"flag": "rot_heard"}},
             {"id": "s2", "summary": "find and destroy what fouls the backwater",
              "resolutions": [{"id": "cleansed", "flag": "rot_cleared"}]}],
         "journal": {
             "offered": "Perrin says something dead in the reeds is poisoning the town well.",
             "s1": "It's laid up downriver, where the ford dies in the willows. Go armed.",
             "resolved.cleansed": "The rot is burned. The well runs clean."}},
        {"id": "obj_debt", "tension": "tension_water", "archetype": "moral_fork",
         "title": "The Collector's Due", "main": False,
         "steps": [
             {"id": "s1", "summary": "hear Tomas out", "advance": {"flag": "debt_heard"}},
             {"id": "s2", "summary": "answer the levy before the collector comes",
              "resolutions": [
                  {"id": "paid", "flag": "debt_paid", "requires": {"item": "coin_pouch"}},
                  {"id": "refused", "flag": "debt_refused"}]}],
         "journal": {
             "offered": "Tomas can't pay tomorrow's water-levy.",
             "s1": "He needs coin, or the nerve to refuse. The mill may still hide the miller's purse.",
             "resolved.paid": "You covered the levy. Tomas keeps his land another year.",
             "resolved.refused": "You told him to stand."}},
    ]}


def _gold_artifact():
    art = _ir_to_artifact(load_example("world_game"))
    # The Low Water is ONE tension web ("one axis, three pulls" — the notes): a single main
    # tension all three quests hang off, which demand must accept as fully covered.
    art["bible"] = {"setting": "the barony of Vessle is dying of thirst",
                    "factions": [{"id": "fac_guild", "name": "Guild", "wants": "the gate open"},
                                 {"id": "fac_keep", "name": "Keep", "wants": "the levy paid"}],
                    "tensions": [{"id": "tension_water",
                                  "summary": "the baron's weir starves the town",
                                  "between": ["fac_guild", "fac_keep"], "scale": "main"}]}
    art["objectives"] = _low_water_objectives()
    return art


def test_the_notes_sketch_validates_against_the_gold_game():
    art = _gold_artifact()
    assert O.v_objectives(art["objectives"]) is None
    # the whole module reads clean over the shipped game: every tension covered, mains agree,
    # every producer in order (kel_defeated via enc_kel's on_victory node, sluice_crank via the
    # mill take, weir_open behind the two-key sluice), journal complete, endings reachable
    spec = {"params": {}, "modules": ["bible", "objectives", "world", "scenes", "inventory"]}
    assert OBJ.get_errors(make_ctx(spec, art)) == []


def test_gold_game_ordering_breaks_when_the_mill_is_gated_on_the_endgame():
    # regate every entrance to old_mill (the crank's producer) on weir_open — the final flag —
    # and the s2 inversion the notes hand-verified with a script becomes a deterministic error
    art = _gold_artifact()
    for p in art["places"]["places"].values():
        for h in p.get("interactables", []):
            a = h.get("action", {})
            if a.get("type") == "move" and a.get("target") == "old_mill":
                a["requires"] = {"flag": "weir_open"}
    spec = {"params": {}, "modules": ["bible", "objectives", "world", "scenes", "inventory"]}
    errs = [e for e in OBJ.get_errors(make_ctx(spec, art)) if e.code == "producer_order"]
    assert [(e.path, e.ref) for e in errs] == [("obj_weir.s2", "sluice_crank")]


def test_gold_ir_with_objectives_compiles_to_godot(tmp_path):
    # the runtime contract end-to-end: components -> assemble -> schema+crossref -> game.json
    # carries ir.objectives for the journal/HUD render
    from godot.compiler import compile_godot
    art = _gold_artifact()
    for cid, content in art.items():
        (tmp_path / f"{cid}.json").write_text(json.dumps(content))
    (tmp_path / "asset_manifest.json").write_text(json.dumps(
        {"backgrounds": [], "characters": []}))
    (tmp_path / "spec.json").write_text(json.dumps(
        {"modules": ["bible", "objectives", "world", "scenes"], "engine": "godot"}))
    result = compile_godot(tmp_path, distribute=False)
    assert result["ok"], result.get("reason")
    game = json.loads((tmp_path / "godot_output" / "game.json").read_text())
    assert [o["id"] for o in game["objectives"]] == ["obj_weir", "obj_water", "obj_debt"]
    assert all("tension" not in o and "archetype" not in o for o in game["objectives"])


# ── composition + routing (combat precedent: godot-only, auto-routed) ─────────
def test_objectives_routes_the_engine_to_godot():
    from maestro.modules import engine_for, expand_modules
    mods = expand_modules(["world", "objectives", "scenes", "cast", "inventory"])
    assert "bible" in mods            # requires pulls the demand source in
    assert engine_for(mods) == "godot"   # registers projections, then routes
    assert unprojectable("renpy", mods) == ["objectives"]   # no Ren'Py projection, by design
    assert unprojectable("godot", mods) == []
    assert OBJ.layer == "engine"
    assert "objectives" not in dict(selectable_catalog())   # engine layer: hidden from the picker
    # NOT always-on: selectable=False would force this godot-only module into every composition
    # and reroute plain VNs off renpy — it must enter only via requires/picks
    from maestro.modules.module import _forced_ids
    assert "objectives" not in _forced_ids()
    assert engine_for(expand_modules(["scenes", "cast", "story"])) == "renpy"


# ── the runtime surface (test_godot_runtime_gd.py style string asserts) ───────
def test_runtime_renders_journal_and_hud_from_ir_objectives():
    src = (_RUNTIME / "Game.gd").read_text()
    # J is bound; the journal panel exists and is reachable from the pause menu too
    assert '"ui_journal": [KEY_J]' in src
    assert "func journal_panel(" in src
    pause = src.split("func pause_menu(")[1].split("\nfunc ")[0]
    assert "Journal" in pause and "journal_panel()" in src.split("func pause_menu(")[1]
    # quest state is DERIVED from flags/items at read time — no new runtime state store
    stage = src.split("func objective_stage(")[1].split("\nfunc ")[0]
    assert "IRCore.eval_cond" in stage and 'state["' not in stage.replace(
        "IRCore.eval_cond(state,", "")
    assert '"resolved.%s"' in stage
    # the HUD carries the main objective's current step, refreshed with the frame sweep
    proc = src.split("func _process(")[1].split("\nfunc ")[0]
    assert "_update_objective_hud()" in proc
    hud = src.split("func _update_objective_hud(")[1].split("\nfunc ")[0]
    assert 'o.get("main"' in hud and '"step"' in hud


def test_runtime_gated_verbs_name_the_missing_requirement():
    src = (_RUNTIME / "Game.gd").read_text()
    run_action = src.split("func run_action(")[1].split("\n# ")[0]
    # every bare denial ("Not yet.") now renders the requires condition in plain words
    assert 'gate_text("You can\'t go that way yet.", act["requires"])' in run_action
    assert 'gate_text("Not yet.", gate)' in run_action
    assert 'gate_text("Not now.", act["requires"])' in run_action
    unmet = src.split("func _unmet_names(")[1].split("\nfunc ")[0]
    assert 'item_by_id.get(cond["item"], {}).get("name")' in unmet   # items by display name


@pytest.mark.parametrize("presenter", ["overworld.gd", "pnc.gd", "overworld3d.gd"])
def test_presenters_poll_the_journal_key(presenter):
    src = (_RUNTIME / presenter).read_text()
    assert 'Input.is_action_just_pressed("ui_journal")' in src
    assert "journal_panel()" in src
