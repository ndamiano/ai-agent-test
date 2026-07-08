"""Per-error fix prompts for the `world` module. Each world repair check that used to ride the
generic `places_fix.txt` menu now points at a CUSTOM, laser-focused fix prompt (exact tool + exact
ids + guardrail), mirroring the shipped `crossref_*` exemplars. These tests pin the check->prompt
wiring, the lean `ctx_structural` archetype, and that each prompt loads (includes resolved) and
carries its specific guidance."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro import context_render as cr
from maestro.modules.context import Context
from maestro.modules.module import load_prompt
from maestro.modules.world import MODULE as WORLD


# The migrated repair checks and the custom prompt each now owns.
_CHECK_PROMPT = {
    "start_authored": "world_start_fix.txt",
    "places_reachable": "world_orphan_fix.txt",
    "rpg_layout": "world_layout_fix.txt",
    "rpg_connectivity": "world_return_fix.txt",
    "nodes_entered": "world_entry_fix.txt",
    "compiles": "places_compile_fix.txt",
}


class _State:
    run_dir = "/tmp/none"

    def load_artifact(self):
        return {}

    def read_story_state(self):
        return {}

    def read_scratchpad(self):
        return {}


def _ctx(artifact, modules):
    return Context(spec={"modules": modules, "params": {"min_places": 1, "min_interactables": 1},
                         "engine": "godot", "title": "T"},
                   state=_State(), artifact=artifact)


# ── wiring: each check owns its custom prompt + the lean structural archetype ──

def test_no_world_check_still_rides_the_generic_places_fix():
    for chk in WORLD.checks:
        assert getattr(chk, "prompt", None) != "places_fix.txt", chk.code


def test_check_to_prompt_map():
    for code, prompt in _CHECK_PROMPT.items():
        chk = WORLD._check_for(code)
        assert chk is not None, code
        assert chk.prompt == prompt, f"{code} -> {chk.prompt!r}, expected {prompt!r}"


def test_migrated_checks_use_ctx_structural():
    for code in _CHECK_PROMPT:
        chk = WORLD._check_for(code)
        assert chk.context is cr.ctx_structural, code


def test_migrated_checks_kept_their_tool_scope():
    # A fix can only call tools scoped to its step; the migration must not have widened/narrowed it.
    assert "set_places_meta" in WORLD._check_for("start_authored").tools
    assert "write_place" in WORLD._check_for("start_authored").tools
    assert "add_interactable" in WORLD._check_for("places_reachable").tools
    assert "write_place" in WORLD._check_for("rpg_layout").tools
    assert "add_interactable" in WORLD._check_for("rpg_connectivity").tools
    assert {"add_interactable", "edit_node"} <= WORLD._check_for("nodes_entered").tools
    assert {"edit_place", "add_interactable"} <= WORLD._check_for("compiles").tools


# ── each prompt loads (includes resolved) and demands a tool call ─────────────

def test_all_fix_prompts_load_with_includes_resolved():
    for prompt in set(_CHECK_PROMPT.values()):
        text = load_prompt(prompt)
        assert "{{include" not in text, prompt          # partials inlined
        assert "TOOL CALL" in text, prompt              # tool_call_rule partial present


# ── each prompt carries its SPECIFIC repair (tool + guardrail), not a menu ────

@pytest.mark.parametrize("prompt, present, any_of", [
    # start: offers BOTH repairs and authors under the EXACT phantom id (verbatim)
    ("world_start_fix.txt",
     ["set_places_meta(start_place=", "write_place(place_id=", "verbatim"], []),
    # orphan: adds a move to make the place REACHABLE, forbids repointing an existing route
    ("world_orphan_fix.txt",
     ["add_interactable(place_id=", "REACHABLE", "repointing an EXISTING"], []),
    # layout: a WALKABLE tile-map repair that DISOWNS point-and-click (the old prompt's bug)
    ("world_layout_fix.txt",
     ["WALKABLE", "WASD", "NOT a point-and-click", "write_place(place_id=",
      "edit_place(place_id=", '"spawn": {"feature":'],
     [['NEVER a {"rect":...}', 'NEVER a {"rect": ...}']]),
    # return: adds a back route without touching the forward move
    ("world_return_fix.txt",
     ["add_interactable(place_id=", '"type": "move"', "do NOT delete or repoint the forward move"],
     []),
    # entry: wires a talk into the dead scene (or repoints an entered scene)
    ("world_entry_fix.txt",
     ['"type": "talk"', "add_interactable(place_id=", "edit_node(node_id="], []),
    # compile: keys on the bracketed place, reads then edits it
    ("places_compile_fix.txt",
     ["brackets", "read_place(place_id=", "edit_place(place_id=", "add_interactable(place_id="],
     []),
], ids=["start", "orphan", "layout", "return", "entry", "compile"])
def test_fix_prompt_carries_specific_repair(prompt, present, any_of):
    t = load_prompt(prompt)
    for s in present:
        assert s in t, (prompt, s)
    for group in any_of:
        assert any(s in t for s in group), (prompt, group)


# ── integration: get_correction_prompt actually renders the new prompt + a lean
#    structural user payload (target + places index, no cross-module prose) ────

_ART_START = {"places": {"start_place": "phantom", "place_ids": ["z1"],
                         "places": {"z1": {"kind": "room", "interactables": [
                             {"id": "h1", "action": {"type": "examine", "text": "t"}}]}}}}
# A combat game whose only zone walls its enemy off -> rpg_layout fires -> tile-map fix prompt.
_ART_LAYOUT = {"places": {
    "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}}, "place_ids": ["z1"],
    "places": {"z1": {"kind": "world_map", "tiles": {"legend": {}, "rows": ["..#.."]},
                      "interactables": [
                          {"id": "enemy", "position": {"cell": {"x": 4, "y": 0}},
                           "action": {"type": "start_combat", "encounter": "e"}},
                          {"id": "sign", "position": {"cell": {"x": 1, "y": 0}},
                           "action": {"type": "examine", "text": "t"}}]}}}}


@pytest.mark.parametrize("art, modules, code, sys_startswith, sys_in, user_in, tools_in", [
    # start_authored: renders world_start_fix.txt (not the menu) over a ctx_structural places index
    (_ART_START, ["world", "scenes"], "start_authored", "`start_place` names a place", [],
     ["start_authored", "phantom", "PLACES (start:", "z1"], ["set_places_meta"]),
    # rpg_layout: renders the tile-map prompt, names the specific walled-off defect in the target
    (_ART_LAYOUT, ["world", "scenes", "combat"], "rpg_layout", None, ["WALKABLE TILE MAP"],
     ["walled off from the spawn"], []),
], ids=["start_authored", "rpg_layout"])
def test_correction_renders_new_prompt_and_structural_user(
        art, modules, code, sys_startswith, sys_in, user_in, tools_in):
    ctx = _ctx(art, modules)
    chk = WORLD._check_for(code)
    errs = chk.detect(chk, WORLD, ctx)
    assert errs
    cp = WORLD.get_correction_prompt(ctx, errs[0])
    if sys_startswith is not None:
        assert cp.system.startswith(sys_startswith)
    for s in sys_in:
        assert s in cp.system, s
    for u in user_in:
        assert u in cp.user, u
    for t in tools_in:
        assert t in cp.allowed_tools, t
