"""Per-error repair prompts for the CONTENT modules (cast / story / assets / inventory).

Their post-authoring safety checks (missing field, duplicate id, ref-resolution) used to leave
`prompt`/`tools`/`skeleton` unset, so a surgical one-field or one-dup fix fell back to the module's
whole-component AUTHORING prompt + its authoring skeleton — wasteful and an invitation to clobber
the whole component. Each now points at a SPECIFIC repair prompt scoped to a read+write pair, with
the authoring skeleton suppressed. These tests pin that wiring and the built correction prompts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.modules.context import Context
from maestro.modules.module import MODULE_REGISTRY, load_prompt, skeleton_guide


def _ctx(spec, art):
    class S:
        run_dir = "/tmp/none"
        def load_artifact(self): return art
        def read_story_state(self): return {}
        def read_waivers(self): return []
        def read_human_todos(self): return []
    return Context(spec=spec, state=S(), artifact=art)


def _fire(module_id, spec, art, code):
    """Build the module's correction prompt for the (single) error whose check has `code`."""
    m = MODULE_REGISTRY[module_id]
    ctx = _ctx(spec, art)
    errs = [e for e in m.get_errors(ctx) if e.code == code]
    assert errs, f"expected a {code!r} error from {module_id}, got {[e.code for e in m.get_errors(ctx)]}"
    return m, m.get_correction_prompt(ctx, errs[0])


# ── the wiring: every repair check names a specific prompt (not the authoring one) + a read/write
#    scope, and suppresses the authoring skeleton ─────────────────────────────────────────────────
_REPAIRS = {
    "cast": {"character_fields": "cast_field_patch.txt",
             "distinct_characters": "cast_rename_duplicate.txt"},
    "story": {"beat_fields": "story_field_patch.txt",
              "distinct_beats": "story_rename_duplicate.txt",
              "distinct_endings": "story_rename_duplicate.txt",
              "ending_path_fields": "story_field_patch.txt",
              "endings_planned": "story_endings_plan_fix.txt"},
    "assets": {"background_ids": "assets_background_field_patch.txt",
               "character_ids": "assets_character_ids_fix.txt"},
    "inventory": {"item_fields": "inventory_field_patch.txt",
                  "distinct_items": "inventory_rename_duplicate.txt"},
}


def test_repair_checks_are_wired_to_specific_prompts():
    for mid, mapping in _REPAIRS.items():
        m = MODULE_REGISTRY[mid]
        for code, prompt_file in mapping.items():
            chk = m._check_for(code)
            assert chk is not None, f"{mid} has no check {code!r}"
            assert chk.prompt == prompt_file, f"{mid}.{code} -> {chk.prompt!r} != {prompt_file!r}"
            # never falls back to the whole-component authoring prompt
            assert chk.prompt != m.mode_prompt
            # a surgical read + write scope, and NOT the append-only authoring tool
            assert chk.tools == frozenset({"read_component", "write_component", "request_review"})
            for add_tool in ("add_character", "add_beat", "add_ending", "add_item"):
                assert add_tool not in chk.tools
            # the authoring skeleton is suppressed (empty string, not None -> module default)
            assert chk.skeleton == ""


def test_repair_prompts_render_and_carry_tool_call_rule():
    for mapping in _REPAIRS.values():
        for prompt_file in set(mapping.values()):
            text = load_prompt(prompt_file)
            assert "TOOL CALL" in text, f"{prompt_file} missing the tool_call_rule partial"
            assert "{{include" not in text, f"{prompt_file} left an unresolved include"


# ── cast ──────────────────────────────────────────────────────────────────────
def test_cast_field_patch_is_surgical_not_authoring():
    art = {"characters": {"characters": [
        {"id": "mara", "name": "Mara", "voice": "curt"},
        {"id": "jon", "name": "Jon"}]}}  # jon missing voice
    m, p = _fire("cast", {"params": {"character_fields": ["id", "name", "voice"]}}, art,
                 "character_fields")
    assert "read_component" in p.system and "write_component" in p.system
    assert "read_component" in p.allowed_tools and "write_component" in p.allowed_tools
    assert "add_character" not in p.allowed_tools
    # the target names the exact offending path
    assert "characters.characters[1] missing 'voice'" in p.user
    # the authoring skeleton is NOT appended (no clobber-invite)
    assert skeleton_guide("characters", "") not in p.system
    assert "JSON SHAPE" not in p.system


def test_cast_rename_duplicate_targets_the_dup_id():
    art = {"characters": {"characters": [
        {"id": "mara", "name": "Mara"}, {"id": "mara", "name": "Other"}]}}
    m, p = _fire("cast", {"params": {}}, art, "distinct_characters")
    assert "unique" in p.system.lower()
    assert "duplicate values: ['mara']" in p.user
    assert "JSON SHAPE" not in p.system


# ── story ─────────────────────────────────────────────────────────────────────
_STORY_PARAMS = {"params": {"min_beats": 1, "min_endings": 1}}


def test_story_beat_field_patch():
    art = {"story": {"central_question": "Q?",
                     "beats": [{"id": "b1", "summary": "s", "type": "plot", "purpose": "setup"}],
                     "endings": [{"id": "e1", "description": "d"}],
                     "ending_paths": [{"ending": "e1", "earned_by": "the b1 choice"}]}}
    m, p = _fire("story", _STORY_PARAMS, art, "beat_fields")
    assert "story.beats[0] missing 'tension'" in p.user
    assert "write_component" in p.system
    assert "JSON SHAPE" not in p.system


def test_story_endings_plan_fix_is_ref_resolution():
    # e2 exists but no ending_paths entry names it -> unreachable ending
    art = {"story": {"central_question": "Q?",
                     "beats": [{"id": "b1", "summary": "s", "type": "plot",
                                "purpose": "setup", "tension": "none"}],
                     "endings": [{"id": "e1", "description": "d1"},
                                 {"id": "e2", "description": "d2"}],
                     "ending_paths": [{"ending": "e1", "earned_by": "the b1 choice"}]}}
    m, p = _fire("story", _STORY_PARAMS, art, "endings_planned")
    # names the orphaned ending
    assert "e2" in p.user
    # asks for the ONE missing ending_paths entry
    assert "ending_paths" in p.system and "earned_by" in p.system
    # hands the valid beat + ending id lists (via ctx_structural self-view)
    assert "b1" in p.user and "e1" in p.user
    assert "write_component" in p.system


def test_story_distinct_endings_uses_rename_prompt():
    art = {"story": {"central_question": "Q?",
                     "beats": [{"id": "b1", "summary": "s", "type": "plot",
                                "purpose": "setup", "tension": "none"}],
                     "endings": [{"id": "e1", "description": "d1"},
                                 {"id": "e1", "description": "d2"}],
                     "ending_paths": [{"ending": "e1", "earned_by": "the b1 choice"}]}}
    m, p = _fire("story", _STORY_PARAMS, art, "distinct_endings")
    assert "duplicate values: ['e1']" in p.user
    assert "ending_paths" in p.system  # reminds to sync the plan on a rename


# ── assets ────────────────────────────────────────────────────────────────────
def test_assets_background_field_patch():
    art = {"asset_manifest": {"backgrounds": [{"id": "bg_a", "description": "hall"},
                                              {"description": "no id"}],
                              "characters": [], "cgs": []}}
    m, p = _fire("assets", {"params": {}}, art, "background_ids")
    assert "asset_manifest.backgrounds[1] missing 'id'" in p.user
    assert "read_component" in p.allowed_tools  # assets mode_tools omit it; the repair scope adds it
    assert "JSON SHAPE" not in p.system


def test_assets_character_ids_fix_hands_the_cast_roster():
    art = {"characters": {"characters": [{"id": "mara", "name": "Mara", "role": "protagonist"}]},
           "asset_manifest": {"backgrounds": [{"id": "bg_a", "description": "hall"}],
                              "characters": [{"image_file": "x.png", "description": "the woman"}],
                              "cgs": []}}
    m, p = _fire("assets", {"params": {}}, art, "character_ids")
    # crossref-shaped: the exact cast id roster is in the user message to pick from
    assert "mara" in p.user
    # and the system prompt says match a cast id as a plain string
    assert "cast id" in p.system.lower() and "plain string" in p.system.lower()
    assert "read_component" in p.allowed_tools


# ── inventory ─────────────────────────────────────────────────────────────────
def test_inventory_field_patch_scoped_to_write_not_add_item():
    art = {"items": {"items": [{"id": "item_key"}]}}  # missing name
    m, p = _fire("inventory", {"params": {}}, art, "item_fields")
    assert "items.items[0] missing 'name'" in p.user
    assert "write_component" in p.allowed_tools
    assert "add_item" not in p.allowed_tools   # the old prose said add_item while scoped to write
    assert "JSON SHAPE" not in p.system


def test_inventory_distinct_items_drops_the_duplicate():
    art = {"items": {"items": [{"id": "item_key", "name": "Key"},
                               {"id": "item_key", "name": "Key2"}]}}
    m, p = _fire("inventory", {"params": {}}, art, "distinct_items")
    assert "duplicate values: ['item_key']" in p.user
    assert "redundant" in p.system.lower()
