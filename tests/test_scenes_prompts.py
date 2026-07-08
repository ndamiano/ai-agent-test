"""scenes module: every repair check builds its OWN specific fix prompt (not the generic
`nodes_fix.txt` menu and not the whole-component authoring prompt). Each test drives
`get_correction_prompt` for one error code and asserts the right system prompt, tool scope, and
that the relevant catalogue / target locator is in the user payload."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.modules.context import Context
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
            "central_question": "Do they leave?",
            "beats": [{"id": "beat_01", "summary": "the argument", "purpose": "setup"},
                      {"id": "beat_02", "summary": "the turn", "purpose": "midpoint_turn"},
                      {"id": "beat_03", "summary": "the crisis", "purpose": "crisis"},
                      {"id": "beat_04", "summary": "the end", "purpose": "resolution"}],
            "endings": [{"id": "ending_solitude", "description": "she leaves the office alone"}],
            "ending_paths": [{"ending": "ending_solitude",
                              "earned_by": "the beat_04 choice to stay"}],
        },
        "items": {"items": [{"id": "item_key", "name": "Brass Key"}]},
    }


class _State:
    run_dir = "/tmp/none"
    def read_story_state(self): return {}
    def load_artifact(self): return _art()


def _ctx():
    return Context(spec={"title": "The Office", "concept": "a leaving", "request": "make it",
                         "modules": ["cast", "story", "scenes"], "params": {}},
                   state=_State(), artifact=_art())


def _prompt(code, **err_kw):
    err = Error(type=err_kw.pop("type", ErrorType.FIX), code=code, component="nodes", **err_kw)
    return SCENES.get_correction_prompt(_ctx(), err)


def _tset(tools):
    return tuple(sorted(tools))


# ── the terminal compile fix ───────────────────────────────────────────────────
def test_compiles_gets_compile_fix_prompt():
    cp = _prompt("compiles", message="compile failed: [scene_02] speaker 'ghost' undefined")
    assert "FAILED to compile" in cp.system
    assert "BAD SPEAKER" in cp.system and "MALFORMED END" in cp.system
    assert "COMMON FAILURES" not in cp.system          # not the generic menu
    assert cp.allowed_tools == _tset(_T_EDIT_WRITE)
    # ctx_crossref gives the id catalogues a repoint needs
    assert "CHARACTERS" in cp.user and "LOCATIONS" in cp.user
    assert "scene_02" in cp.user                        # the attributed target


def test_premature_ending_prompt():
    cp = _prompt("premature_endings", path="scene_02",
                 message="scene 'scene_02' ends the game while the story has barely started")
    assert "ENDS the game too early" in cp.system
    assert "PLANNED" in cp.system                       # leave planned endings alone
    assert cp.allowed_tools == _tset(_T_EDIT)
    assert "scene_02" in cp.user


def test_no_dead_gates_prompt():
    cp = _prompt("no_dead_gates",
                 message="choices gated on state that is never raised: scene_02 (gates on 'has_key')")
    assert "GATED on state" in cp.system
    assert "set_flag" in cp.system and "add_var" in cp.system
    assert cp.allowed_tools == _tset(_T_EDIT)
    assert "SCENES" in cp.user                          # structural self-view of the graph


def test_reachable_from_start_prompt():
    cp = _prompt("reachable_from_start", message="nodes unreachable from 'scene_01': ['scene_02']")
    assert "ORPHAN" in cp.system
    assert "recreate" in cp.system                      # don't recreate the orphan
    assert cp.allowed_tools == _tset(_T_EDIT)
    assert "scene_01" in cp.user


def test_each_node_has_location_prompt():
    cp = _prompt("each_node_has_location", message="nodes with no location/background: ['scene_02']")
    assert "no background" in cp.system
    assert "location" in cp.system
    assert cp.allowed_tools == _tset(_T_EDIT)
    # ctx_crossref surfaces the valid background ids
    assert "LOCATIONS" in cp.user and "bg_office" in cp.user


def test_min_branches_prompt():
    cp = _prompt("min_branches", type=ErrorType.BUILD,
                 message="only 0 menu(s), need 1 — add player choices")
    assert "too few player CHOICES" in cp.system
    assert "DIFFERENT" in cp.system                     # two distinct targets
    assert cp.allowed_tools == _tset(_T_EDIT_WRITE)
    assert "SCENES" in cp.user


def test_all_characters_speak_prompt():
    cp = _prompt("all_characters_speak", type=ErrorType.BUILD,
                 message="characters who never speak: ['silent_sam'] — give them lines")
    assert "NEVER speak" in cp.system
    assert "read_node" in cp.system and "write_node" in cp.system   # the append-via-rewrite path
    assert cp.allowed_tools == _tset(_T_EDIT_WRITE)
    assert "silent_sam" in cp.user


def test_each_node_min_lines_prompt():
    cp = _prompt("each_node_min_lines", type=ErrorType.BUILD,
                 message="nodes with < 3 lines: ['scene_02 (1)']")
    assert "too THIN" in cp.system
    assert "verbatim" in cp.system                      # keep existing lines
    assert cp.allowed_tools == _tset(_T_EDIT_WRITE)
    assert "scene_02" in cp.user


def test_endings_are_nodes_prompt():
    cp = _prompt("endings_are_nodes", type=ErrorType.BUILD,
                 message="story.endings → nodes.node_ids: unresolved refs ['ending_solitude']")
    assert "declares an ENDING that has no scene" in cp.system
    assert "write_node" in cp.system and '{"type":"end"}' in cp.system
    assert cp.allowed_tools == _tset(_T_EDIT_WRITE)
    # authoring context carries the ending's description so the scene can be written
    assert "ending_solitude" in cp.user
    # the whole-component skeleton is suppressed for this single-node create
    assert "node_ids" not in cp.system


# ── the whole set: no check still rides the generic menu ─────────────────────────
def test_no_scenes_check_uses_generic_menu_or_authoring_default():
    for chk in SCENES.checks:
        # skip the deterministic (no-LLM) and bespoke-builder checks
        if chk.run is not None or chk.build_prompt is not None:
            continue
        assert chk.prompt not in (None, "nodes_fix.txt"), \
            f"{chk.code} still rides a generic/authoring-default prompt ({chk.prompt!r})"
