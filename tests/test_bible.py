"""bible — the world-game root module (W1).

Pins the contract: the structural validators (accept the design-doc example, reject the four
malformations), the check ordering (blocking setting → tension slots fan → floor/scale/ref safety),
the slice tools' round-trip + guards, the presentation block, and the invariant that the bible is
NEVER lifted into the IR (authoring-state only, like story)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from conftest import make_ctx, seed_frozen_run
from maestro.ir_assemble import assemble_ir
from maestro.modules import compose
from maestro.modules.module import MODULE_REGISTRY, ErrorType, unprojectable, selectable_catalog
from maestro.modules import bible as B
from maestro.tools import build_tools

BIBLE = MODULE_REGISTRY["bible"]
_P = {"params": {"min_tensions": 3}}


# The design-doc example (tasks/world_first.md "The model" → bible), trimmed to the floor of 3
# tensions (one 'main', two 'side') so it is a complete, valid bible.
def _example():
    return {"bible": {
        "setting": "a drought-starved river barony, iron-age tech, superstitious",
        "factions": [
            {"id": "fac_guild", "name": "Smith's Guild", "wants": "the baron's levy repealed"},
            {"id": "fac_keep", "name": "the Baron's men", "wants": "order and the levy paid"}],
        "tensions": [
            {"id": "tension_levy", "summary": "the levy is bleeding the town dry",
             "between": ["fac_guild", "fac_keep"], "scale": "main"},
            {"id": "tension_mine", "summary": "something in the flooded mine kills prospectors",
             "between": ["fac_keep"], "scale": "side"},
            {"id": "tension_faith", "summary": "the priests blame the guild for the drought",
             "between": ["fac_guild"], "scale": "side"}]}}


# ── structural validators ─────────────────────────────────────────────────────
def test_v_bible_accepts_the_design_example():
    assert B.v_bible(_example()["bible"]) is None


def _mutate(fn):
    b = _example()["bible"]
    fn(b)
    return b


@pytest.mark.parametrize("mutate, needle", [
    # no 'main' tension — the world has no central conflict
    (lambda b: b["tensions"][0].__setitem__("scale", "side"), "exactly one"),
    # two 'main' tensions — ambiguous central conflict
    (lambda b: b["tensions"][1].__setitem__("scale", "main"), "exactly one"),
    # a tension names a faction that isn't declared
    (lambda b: b["tensions"][1].__setitem__("between", ["fac_ghost"]), "undeclared faction"),
    # empty setting — the world has no premise
    (lambda b: b.__setitem__("setting", ""), "setting"),
], ids=["no_main", "two_mains", "unknown_faction_ref", "empty_setting"])
def test_v_bible_rejects_malformations(mutate, needle):
    err = B.v_bible(_mutate(mutate))
    assert err and needle in err


@pytest.mark.parametrize("f, ok", [
    ({"id": "fac_a", "name": "A", "wants": "power"}, True),
    ({"id": "fac_a", "name": "A"}, False),           # missing 'wants'
    ({"name": "A", "wants": "power"}, False),         # missing 'id'
    ("not a dict", False),
])
def test_v_faction_structural(f, ok):
    assert (B.v_faction(f) is None) is ok


@pytest.mark.parametrize("t, ok", [
    ({"id": "t1", "summary": "s", "scale": "main", "between": ["fac_a"]}, True),
    ({"id": "t1", "summary": "s", "scale": "loud", "between": ["fac_a"]}, False),  # bad scale
    ({"id": "t1", "summary": "s", "scale": "main", "between": []}, False),          # empty between
    ({"id": "t1", "scale": "main", "between": ["fac_a"]}, False),                   # no summary
])
def test_v_tension_structural(t, ok):
    assert (B.v_tension(t) is None) is ok


# ── check ordering + fan-out ──────────────────────────────────────────────────
def test_setting_blocks_before_everything_else():
    # An empty artifact yields ONLY the blocking setting error — the tension/ref checks below it are
    # suppressed until the world has a premise to hang them on.
    codes = [e.code for e in BIBLE.get_errors(make_ctx(_P, {}))]
    assert codes == ["setting"]
    assert BIBLE._check_for("setting").blocking is True


def test_tension_floor_fans_one_create_per_owed_slot():
    art = {"bible": {"setting": "a barony", "factions": [{"id": "fa", "name": "A", "wants": "w"}],
                     "tensions": []}}
    errs = [e for e in BIBLE.get_errors(make_ctx(_P, art)) if e.code == "tension_floor"]
    assert len(errs) == 3 and all(e.type is ErrorType.BUILD for e in errs)
    assert {e.path for e in errs} == {"#001", "#002", "#003"}   # distinct identity per owed slot
    guard = BIBLE._check_for("tension_floor").guard
    assert guard["count_tool"] == "add_tension" and guard["id_list_key"] == "tension_ids"
    # authored ONE at a time (each tension sees the priors + names the main one first)
    assert guard["cap"](None) == 1


def test_floor_respected_and_clean_when_met():
    # min_tensions satisfied, one main, refs resolve -> no errors at all
    assert BIBLE.get_errors(make_ctx(_P, _example())) == []
    # authoring one shrinks the owed set (visible progress)
    art = {"bible": {"setting": "x", "factions": [{"id": "fa", "name": "A", "wants": "w"}],
                     "tensions": [{"id": "t1", "summary": "s", "scale": "main", "between": ["fa"]}]}}
    assert len([e for e in BIBLE.get_errors(make_ctx(_P, art)) if e.code == "tension_floor"]) == 2


def test_one_main_tension_check_fires_only_off_the_floor():
    # floor met but NO main tension -> the scale check emits a single FIX to add the central one
    art = _example()
    for t in art["bible"]["tensions"]:
        t["scale"] = "side"
    errs = [e for e in BIBLE.get_errors(make_ctx(_P, art)) if e.code == "one_main_tension"]
    assert len(errs) == 1 and errs[0].type is ErrorType.FIX
    # with tensions absent the check is silent (the floor owns emptiness, not the scale check)
    assert not any(e.code == "one_main_tension"
                   for e in BIBLE.get_errors(make_ctx(_P, {"bible": {"setting": "x"}})))


def test_faction_refs_fan_one_fix_per_dangling_id():
    art = {"bible": {"setting": "x", "factions": [],
                     "tensions": [{"id": "t1", "summary": "s", "scale": "main",
                                   "between": ["fac_a", "fac_b", "fac_a"]}]}}
    errs = [e for e in BIBLE.get_errors(make_ctx(_P, art)) if e.code == "faction_refs"]
    assert {e.ref for e in errs} == {"fac_a", "fac_b"}          # deduped, one job per missing id
    assert all(e.type is ErrorType.FIX and e.kind == "faction" for e in errs)


# ── slice tools: round-trip + guards ──────────────────────────────────────────
def _tools(tmp_path):
    state = seed_frozen_run(tmp_path, "g", modules=["bible"], params={"min_tensions": 3})
    return build_tools(state.read_spec(), state, compose(["bible"])), state


def test_tools_round_trip(tmp_path):
    tools, state = _tools(tmp_path)
    assert tools["set_bible"]("a drought barony", [{"id": "fac_a", "name": "A", "wants": "x"}])["ok"]
    assert tools["add_faction"]("fac_b", {"name": "B", "wants": "y"})["ok"]
    assert tools["add_tension"]("t1", {"summary": "s", "scale": "main",
                                       "between": ["fac_a", "fac_b"]})["ok"]
    bible = state.load_artifact()["bible"]
    assert bible["setting"] == "a drought barony"
    assert {f["id"] for f in bible["factions"]} == {"fac_a", "fac_b"}
    assert [t["id"] for t in bible["tensions"]] == ["t1"]


def test_tools_refuse_dup_ids_and_second_main(tmp_path):
    tools, _ = _tools(tmp_path)
    tools["set_bible"]("world", [{"id": "fac_a", "name": "A", "wants": "x"}])
    assert not tools["add_faction"]("fac_a", {"name": "A", "wants": "x"})["ok"]      # dup faction
    tools["add_tension"]("t1", {"summary": "s", "scale": "main", "between": ["fac_a"]})
    assert not tools["add_tension"]("t1", {"summary": "s2", "scale": "side",
                                           "between": ["fac_a"]})["ok"]              # dup tension
    # exactly one main — a second scale='main' is refused at write time
    second = tools["add_tension"]("t2", {"summary": "s2", "scale": "main", "between": ["fac_a"]})
    assert not second["ok"] and "main tension already exists" in second["error"]
    # a side tension is fine
    assert tools["add_tension"]("t2", {"summary": "s2", "scale": "side", "between": ["fac_a"]})["ok"]


def test_set_bible_requires_nonempty_setting(tmp_path):
    tools, _ = _tools(tmp_path)
    assert not tools["set_bible"]("")["ok"]


# ── presentation block ────────────────────────────────────────────────────────
def test_bible_block_renders_setting_factions_tensions():
    lines = B.bible_block(_example())
    text = "\n".join(lines)
    assert "setting: a drought-starved river barony" in text
    assert "fac_guild — Smith's Guild: wants the baron's levy repealed" in text
    assert "tension_levy (main) [fac_guild, fac_keep]" in text
    # an unauthored bible contributes nothing to a downstream prompt
    assert B.bible_block({}) == []


# ── the load-bearing invariant: bible is authoring-state, never IR ────────────
def test_bible_is_never_lifted_into_the_ir():
    # A minimal VN-shaped artifact that ALSO carries a bible component. assemble_ir reads a fixed
    # component list; the bible must not leak into it (like story, it is stripped/never-lifted).
    art = {
        "characters": {"characters": [{"id": "al", "name": "Al"}]},
        "asset_manifest": {"backgrounds": [], "characters": []},
        "nodes": {"node_ids": ["n1"], "nodes": {"n1": {
            "lines": [{"speaker": "al", "text": "hi"}], "end": {"type": "end"}}}},
        **_example(),
    }
    ir = assemble_ir(art)
    assert "bible" not in ir
    for leaked in ("setting", "factions", "tensions"):
        assert leaked not in ir


# ── composition + catalog surface ─────────────────────────────────────────────
def test_bible_is_engine_authoring_only_and_composes_cleanly():
    assert BIBLE.layer == "engine" and BIBLE.selectable is False
    assert BIBLE.params() == {"min_tensions": 3}
    # selectable=False -> hidden from the proposer's catalog
    assert "bible" not in dict(selectable_catalog())
    # composes without error and needs no engine projection (authoring-only)
    modules = [m.id for m in compose(["bible"])]
    assert "bible" in modules
    assert unprojectable("renpy", modules) == [] and unprojectable("godot", modules) == []
