"""Per-error fix prompts for the `world` module. Each world repair check that used to ride the
generic `places_fix.txt` menu now points at a CUSTOM, laser-focused fix prompt (exact tool + exact
ids + guardrail), mirroring the shipped `crossref_*` exemplars. These tests pin the check->prompt
wiring, the lean `ctx_structural` archetype, and that each prompt loads (includes resolved) and
carries its specific guidance."""
import sys
from pathlib import Path

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

def test_start_prompt_offers_both_repairs():
    t = load_prompt("world_start_fix.txt")
    assert "set_places_meta(start_place=" in t and "write_place(place_id=" in t
    assert "verbatim" in t                              # author under the EXACT phantom id


def test_orphan_prompt_adds_a_move_and_forbids_repointing():
    t = load_prompt("world_orphan_fix.txt")
    assert "add_interactable(place_id=" in t
    assert "REACHABLE" in t
    assert "repointing an EXISTING" in t                # never break an existing route


def test_layout_prompt_is_a_tile_map_repair_not_point_and_click():
    t = load_prompt("world_layout_fix.txt")
    assert "WALKABLE" in t and "WASD" in t
    # It must DISOWN point-and-click, not steer toward it (the old prompt's bug).
    assert "NOT a point-and-click" in t
    assert "NEVER a {\"rect\":...}" in t or 'NEVER a {"rect": ...}' in t
    assert "write_place(place_id=" in t and 'edit_place(place_id=' in t
    assert '"spawn": {"feature":' in t


def test_return_prompt_adds_a_back_route_without_touching_forward():
    t = load_prompt("world_return_fix.txt")
    assert "add_interactable(place_id=" in t
    assert '"type": "move"' in t
    assert "do NOT delete or repoint the forward move" in t


def test_entry_prompt_wires_a_talk_into_the_dead_scene():
    t = load_prompt("world_entry_fix.txt")
    assert '"type": "talk"' in t
    assert "add_interactable(place_id=" in t
    assert "edit_node(node_id=" in t                    # or repoint an entered scene


def test_compile_prompt_keys_on_the_bracketed_place():
    t = load_prompt("places_compile_fix.txt")
    assert "brackets" in t
    assert "read_place(place_id=" in t
    assert "edit_place(place_id=" in t and "add_interactable(place_id=" in t


# ── integration: get_correction_prompt actually renders the new prompt + a lean
#    structural user payload (target + places index, no cross-module prose) ────

def test_start_authored_correction_uses_new_prompt_and_structural_user():
    art = {"places": {"start_place": "phantom", "place_ids": ["z1"],
                      "places": {"z1": {"kind": "room", "interactables": [
                          {"id": "h1", "action": {"type": "examine", "text": "t"}}]}}}}
    ctx = _ctx(art, ["world", "scenes"])
    chk = WORLD._check_for("start_authored")
    errs = chk.detect(chk, WORLD, ctx)
    assert errs
    cp = WORLD.get_correction_prompt(ctx, errs[0])
    assert cp.system.startswith("`start_place` names a place")   # world_start_fix.txt, not the menu
    assert "start_authored" in cp.user and "phantom" in cp.user  # the target error
    assert "PLACES (start:" in cp.user                           # ctx_structural -> places index
    assert "z1" in cp.user
    assert "set_places_meta" in tuple(cp.allowed_tools) or "set_places_meta" in cp.allowed_tools


def test_layout_correction_uses_tile_map_prompt():
    # A combat game whose only zone walls its enemy off -> rpg_layout fires -> tile-map fix prompt.
    art = {"places": {
        "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}}, "place_ids": ["z1"],
        "places": {"z1": {"kind": "world_map", "tiles": {"legend": {}, "rows": ["..#.."]},
            "interactables": [
                {"id": "enemy", "position": {"cell": {"x": 4, "y": 0}},
                 "action": {"type": "start_combat", "encounter": "e"}},
                {"id": "sign", "position": {"cell": {"x": 1, "y": 0}},
                 "action": {"type": "examine", "text": "t"}}]}}}}
    ctx = _ctx(art, ["world", "scenes", "combat"])
    chk = WORLD._check_for("rpg_layout")
    errs = chk.detect(chk, WORLD, ctx)
    assert errs
    cp = WORLD.get_correction_prompt(ctx, errs[0])
    assert "WALKABLE TILE MAP" in cp.system
    assert "walled off from the spawn" in cp.user       # the specific defect, named in the target
