"""scenes module: every repair check builds its OWN specific fix prompt (not the generic
`nodes_fix.txt` menu and not the whole-component authoring prompt). Each parametrize row drives
`get_correction_prompt` for one error code and asserts the right system prompt, tool scope, and
that the relevant catalogue / target locator is in the user payload."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import make_ctx
from maestro.modules.module import Error, ErrorType
from maestro.modules.scenes import MODULE as SCENES, _T_EDIT, _T_EDIT_WRITE


def _art():
    return {
        "characters": {"characters": [
            {"id": "mara", "name": "Mara", "role": "protagonist"},
            {"id": "jonas", "name": "Jonas", "role": "npc"},
            {"id": "silent_sam", "name": "Sam", "role": "npc"},
        ]},
        "asset_manifest": {"backgrounds": [
            {"id": "bg_office", "description": "a cramped office"},
            {"id": "bg_hall", "description": "an echoing hall"},
        ]},
        "nodes": {
            "node_ids": ["scene_01", "scene_02"],
            "nodes": {
                "scene_01": {"location": "bg_office", "beat": "beat_01",
                             "lines": [{"speaker": "mara", "text": "We should go."},
                                       {"speaker": "jonas", "text": "Not yet."},
                                       {"speaker": None, "text": "A door slams."}],
                             "end": {"type": "jump", "target": "scene_02"}},
                "scene_02": {"location": "bg_hall", "beat": "beat_02",
                             "lines": [{"speaker": "jonas", "text": "Fine."}],
                             "end": {"type": "end"}},
            },
            "synopses": {"scene_01": "they argue about leaving",
                         "scene_02": "jonas relents"},
        },
        "story": {
            "spine": {"theme": "leaving or staying", "tone": "tense"},
            "start_storyline": "sl_main",
            "storylines": [{
                "id": "sl_main", "kind": "main", "premise": "mara decides whether to leave",
                "target_beats": 4,
                "beats": [
                    {"id": "beat_01", "summary": "the argument", "type": "friction",
                     "purpose": "setup", "tension": "none"},
                    {"id": "beat_02", "summary": "the turn", "type": "plot",
                     "purpose": "midpoint_turn", "tension": "rising"},
                    {"id": "beat_03", "summary": "the crisis", "type": "plot",
                     "purpose": "crisis", "tension": "high"},
                    {"id": "beat_04", "summary": "the end", "type": "plot",
                     "purpose": "resolution", "tension": "none"},
                ],
                "terminus": {"type": "game_end",
                             "ending": {"id": "ending_solitude",
                                        "description": "she leaves the office alone"}},
            }],
        },
        "items": {"items": [{"id": "item_key", "name": "Brass Key"}]},
    }


_SPEC = {"title": "The Office", "concept": "a leaving", "request": "make it",
         "modules": ["cast", "story", "scenes"], "params": {}}


def _prompt(**err_kw):
    err = Error(type=err_kw.pop("type", ErrorType.FIX), component="nodes", **err_kw)
    return SCENES.get_correction_prompt(make_ctx(_SPEC, _art()), err)


def _tset(tools):
    return tuple(sorted(tools))


# ── every repair check builds its OWN specific fix prompt: right system prose, right tool scope,
#    and the catalogue / target locator the fix needs — one row per error code ─────────────────────
@pytest.mark.parametrize("err_kw, sys_in, sys_out, tools, user_in", [
    # the terminal compile fix — ctx_crossref hands the id catalogues a repoint needs; NOT the menu
    (dict(code="compiles", message="compile failed: [scene_02] speaker 'ghost' undefined"),
     ["FAILED to compile", "BAD SPEAKER", "MALFORMED END"], ["COMMON FAILURES"],
     _T_EDIT_WRITE, ["CHARACTERS", "LOCATIONS", "scene_02"]),
    # dead gates — add_effect to raise the state a choice gates on, remove_gate to loosen it
    (dict(code="no_dead_gates",
          message="choices gated on state that is never raised: scene_02 (gates on 'has_key')"),
     ["GATED on state", "add_effect", "remove_gate"], [],
     frozenset({"read_node", "add_effect", "remove_gate"}), ["SCENES"]),
    # orphan node — don't recreate it, wire it in
    (dict(code="reachable_from_start", message="nodes unreachable from 'scene_01': ['scene_02']"),
     ["ORPHAN", "recreate"], [], _T_EDIT, ["scene_01"]),
    # missing background — ctx_crossref surfaces the valid background ids
    (dict(code="each_node_has_location", message="nodes with no location/background: ['scene_02']"),
     ["no background", "location"], [], _T_EDIT, ["LOCATIONS", "bg_office"]),
    # too few branches — two DIFFERENT targets
    (dict(code="min_branches", type=ErrorType.BUILD,
          message="only 0 menu(s), need 1 — add player choices"),
     ["too few player CHOICES", "DIFFERENT"], [], _T_EDIT_WRITE, ["SCENES"]),
    # silent character — append lines via the read_node/write_node path
    (dict(code="all_characters_speak", type=ErrorType.BUILD,
          message="characters who never speak: ['silent_sam'] — give them lines"),
     ["NEVER speak", "read_node", "write_node"], [], _T_EDIT_WRITE, ["silent_sam"]),
    # thin node — keep existing lines verbatim
    (dict(code="each_node_min_lines", type=ErrorType.BUILD,
          message="nodes with < 3 lines: ['scene_02 (1)']"),
     ["too THIN", "verbatim"], [], _T_EDIT_WRITE, ["scene_02"]),
], ids=["compiles", "no_dead_gates", "reachable_from_start",
        "each_node_has_location", "min_branches", "all_characters_speak", "each_node_min_lines"])
def test_per_error_repair_prompt(err_kw, sys_in, sys_out, tools, user_in):
    cp = _prompt(**err_kw)
    for s in sys_in:
        assert s in cp.system, s
    for s in sys_out:
        assert s not in cp.system, s
    assert cp.allowed_tools == _tset(tools)
    for u in user_in:
        assert u in cp.user, u


# ── the whole set: no check still rides the generic menu ─────────────────────────
def test_no_scenes_check_uses_generic_menu_or_authoring_default():
    for chk in SCENES.checks:
        # skip the deterministic (no-LLM) and bespoke-builder checks
        if chk.run is not None or chk.build_prompt is not None:
            continue
        assert chk.prompt not in (None, "nodes_fix.txt"), \
            f"{chk.code} still rides a generic/authoring-default prompt ({chk.prompt!r})"
