"""Per-error repair prompts for the CONTENT modules (cast / story / assets / inventory).

Their post-authoring safety checks (missing field, duplicate id, ref-resolution) used to leave
`prompt`/`tools`/`skeleton` unset, so a surgical one-field or one-dup fix fell back to the module's
whole-component AUTHORING prompt + its authoring skeleton — wasteful and an invitation to clobber
the whole component. Each now points at a SPECIFIC repair prompt scoped to a read+write pair, with
the authoring skeleton suppressed. These tests pin that wiring and the built correction prompts.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import make_ctx
from maestro.modules.module import MODULE_REGISTRY, load_prompt, skeleton_guide


def _fire(module_id, spec, art, code):
    """Build the module's correction prompt for the (single) error whose check has `code`."""
    m = MODULE_REGISTRY[module_id]
    ctx = make_ctx(spec, art)
    errs = [e for e in m.get_errors(ctx) if e.code == code]
    assert errs, f"expected a {code!r} error from {module_id}, got {[e.code for e in m.get_errors(ctx)]}"
    return m, m.get_correction_prompt(ctx, errs[0])


# ── the wiring: every repair check names a specific prompt (not the authoring one) + a read/write
#    scope, and suppresses the authoring skeleton ─────────────────────────────────────────────────
_REPAIRS = {
    "cast": {"character_fields": "cast_field_patch.txt",
             "distinct_characters": "cast_rename_duplicate.txt"},
    "story": {"beat_fields": "story_field_patch.txt",
              "distinct_storylines": "story_rename_duplicate.txt"},
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
            for add_tool in ("add_character", "add_beat", "add_storyline", "add_item"):
                assert add_tool not in chk.tools
            # the authoring skeleton is suppressed (empty string, not None -> module default)
            assert chk.skeleton == ""


def test_repair_prompts_render_and_carry_tool_call_rule():
    for mapping in _REPAIRS.values():
        for prompt_file in set(mapping.values()):
            text = load_prompt(prompt_file)
            assert "TOOL CALL" in text, f"{prompt_file} missing the tool_call_rule partial"
            assert "{{include" not in text, f"{prompt_file} left an unresolved include"


# ── the built correction prompts: each repair is surgical (specific target path + read/write scope,
#    no authoring skeleton / clobber-invite), one row per (module, error code) ────────────────────

# arts reused across the story rows (a valid-shaped story that trips exactly one check)
_STORY_PARAMS = {"params": {"min_beats_floor": 1}}
_STORY_BAD_BEAT = {"story": {"spine": {"theme": "T", "tone": "grim"}, "start_storyline": "sl_main",
                             "storylines": [{"id": "sl_main", "kind": "main",
                                            "target_beats": 1,
                                            "beats": [{"id": "b1", "summary": "s", "type": "plot",
                                                       "purpose": "setup", "tension": "none"}],
                                            "terminus": {"type": "game_end",
                                                         "ending": {"id": "e1", "description": "d"}}}]}}
# distinct_storylines: two storylines sharing an id (endings_planned/ending_paths no longer exist —
# an ending is embedded directly in its storyline's game_end terminus, correct by construction)
_STORY_DUP_STORYLINE = {"story": {"spine": {"theme": "T", "tone": "grim"},
                                  "start_storyline": "sl_main",
                                  "storylines": [
                                      {"id": "sl_main", "kind": "main", "premise": "p1",
                                       "target_beats": 1,
                                       "beats": [{"id": "b1", "summary": "s", "type": "plot",
                                                  "purpose": "setup", "tension": "none"}],
                                       "terminus": {"type": "game_end",
                                                    "ending": {"id": "e1", "description": "d"}}},
                                      {"id": "sl_main", "kind": "side", "premise": "p2",
                                       "target_beats": 1,
                                       "beats": [{"id": "b2", "summary": "s2", "type": "plot",
                                                  "purpose": "setup", "tension": "none"}],
                                       "terminus": {"type": "handoff"}},
                                  ]}}


@pytest.mark.parametrize("c", [
    # cast field patch: surgical (read+write in prose AND scope), never add_character, no skeleton
    dict(id="cast_field", module="cast",
         spec={"params": {"character_fields": ["id", "name", "voice"]}},
         art={"characters": {"characters": [
             {"id": "mara", "name": "Mara", "voice": "curt"},
             {"id": "jon", "name": "Jon"}]}},  # jon missing voice
         code="character_fields",
         user_in=["characters.characters[1] missing 'voice'"],
         sys_in=["read_component", "write_component"], sys_out=["JSON SHAPE"],
         tools_in=["read_component", "write_component"], tools_out=["add_character"],
         skeleton_absent=("characters", "")),
    # cast rename duplicate: targets the dup id, asks for uniqueness, no authoring skeleton
    dict(id="cast_rename_dup", module="cast", spec={"params": {}},
         art={"characters": {"characters": [
             {"id": "mara", "name": "Mara"}, {"id": "mara", "name": "Other"}]}},
         code="distinct_characters",
         user_in=["duplicate values: ['mara']"], sys_lower_in=["unique"], sys_out=["JSON SHAPE"]),
    # story storyline field patch: names the exact missing field (premise), write-scoped, no skeleton
    dict(id="story_beat_field", module="story", spec=_STORY_PARAMS, art=_STORY_BAD_BEAT,
         code="beat_fields",
         user_in=["story.storylines[0] missing 'premise'"], sys_in=["write_component"],
         sys_out=["JSON SHAPE"]),
    # story distinct storylines rides the rename prompt on a duplicate storyline id
    dict(id="story_distinct_storylines", module="story", spec=_STORY_PARAMS,
         art=_STORY_DUP_STORYLINE, code="distinct_storylines",
         user_in=["duplicate values: ['sl_main']"], sys_lower_in=["unique"]),
    # assets background field patch: names the offending path; repair scope ADDS read_component
    # (assets mode_tools omit it); no skeleton
    dict(id="assets_bg_field", module="assets", spec={"params": {}},
         art={"asset_manifest": {"backgrounds": [{"id": "bg_a", "description": "hall"},
                                                 {"description": "no id"}],
                                 "characters": [], "cgs": []}},
         code="background_ids",
         user_in=["asset_manifest.backgrounds[1] missing 'id'"], sys_out=["JSON SHAPE"],
         tools_in=["read_component"]),
    # assets character_ids fix is crossref-shaped: hands the cast roster to pick from + says match a
    # cast id as a plain string
    dict(id="assets_char_ids", module="assets", spec={"params": {}},
         art={"characters": {"characters": [{"id": "mara", "name": "Mara", "role": "protagonist"}]},
              "asset_manifest": {"backgrounds": [{"id": "bg_a", "description": "hall"}],
                                 "characters": [{"image_file": "x.png", "description": "the woman"}],
                                 "cgs": []}},
         code="character_ids",
         user_in=["mara"], sys_lower_in=["cast id", "plain string"], tools_in=["read_component"]),
    # inventory field patch: write-scoped, never add_item (old prose said add_item), no skeleton
    dict(id="inventory_field", module="inventory", spec={"params": {}},
         art={"items": {"items": [{"id": "item_key"}]}},  # missing name
         code="item_fields",
         user_in=["items.items[0] missing 'name'"], sys_out=["JSON SHAPE"],
         tools_in=["write_component"], tools_out=["add_item"]),
    # inventory distinct items drops the redundant duplicate
    dict(id="inventory_distinct", module="inventory", spec={"params": {}},
         art={"items": {"items": [{"id": "item_key", "name": "Key"},
                                  {"id": "item_key", "name": "Key2"}]}},
         code="distinct_items",
         user_in=["duplicate values: ['item_key']"], sys_lower_in=["redundant"]),
], ids=lambda c: c["id"])
def test_content_repair_prompt(c):
    m, p = _fire(c["module"], c["spec"], c["art"], c["code"])
    for s in c.get("user_in", []):
        assert s in p.user, s
    for s in c.get("sys_in", []):
        assert s in p.system, s
    for s in c.get("sys_lower_in", []):
        assert s in p.system.lower(), s
    for s in c.get("sys_out", []):
        assert s not in p.system, s
    for t in c.get("tools_in", []):
        assert t in p.allowed_tools, t
    for t in c.get("tools_out", []):
        assert t not in p.allowed_tools, t
    if c.get("skeleton_absent"):
        assert skeleton_guide(*c["skeleton_absent"]) not in p.system
