import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.ir_vn import compile_vn
from maestro.ir_crossref import crossref_errors

_ROOT = Path(__file__).parent.parent
_EXAMPLE = _ROOT / "docs" / "examples" / "vn_crappy.json"


def _ir():
    return json.loads(_EXAMPLE.read_text())


def _out():
    return compile_vn(_ir())


def test_example_is_reference_clean():
    assert crossref_errors(_ir()) == []


def test_characters_defined():
    out = _out()
    for line in ('define al = Character("Al")',
                 'define bo = Character("Bo")',
                 'define cy = Character("Cy")'):
        assert line in out


def test_state_defaults():
    out = _out()
    assert "default told_truth = False" in out
    assert "default apologized = False" in out
    assert "default mood = 0" in out
    assert "default patience = 0" in out
    assert "default inventory = []" in out   # game has items


def test_no_inventory_default_without_items():
    ir = _ir()
    ir.pop("items")
    # strip the item-dependent choice so it stays reference-clean
    ir["nodes"][1]["end"]["choices"] = [c for c in ir["nodes"][1]["end"]["choices"]
                                        if "item" not in (c.get("requires") or {})]
    assert "default inventory" not in compile_vn(ir)


def test_start_jumps_to_start_node():
    assert "label start:\n    jump n_intro" in _out()


def test_narration_vs_speaker_lines():
    out = _out()
    assert '\n    "A dim room. Three guys, one dented flask."' in out
    assert '\n    al "You good?"' in out


def test_quote_and_backslash_escaping_is_automatic():
    out = _out()
    assert r'bo "Define \"good\"."' in out
    # quotes + backslash + apostrophe all survive via json.dumps
    assert r'''cy "He muttered, \"great,\" scrawled a back\\slash, then an 'X'."''' in out


def test_line_effects_set_and_add_var():
    out = _out()
    assert "    $ patience = 2" in out
    assert "    $ mood += 1" in out
    assert "    $ mood += -1" in out          # negative delta


def test_plain_jump_end():
    assert "label n_drink:" in _out()
    assert '    bo "Empty already."\n    $ apologized = True\n    jump n_hub' in _out()


def test_return_end():
    assert '"You press. Silence presses back."\n    $ mood += -1\n    return' in _out()


def test_definitive_end_keeps_ending_comment():
    out = _out()
    assert "return  # ending: quiet" in out
    assert "return  # ending: bad" in out


def test_menu_choice_effects_and_targets():
    out = _out()
    assert '        "Take the flask":\n            $ inventory.append("flask")\n            jump n_hub' in out
    assert '        "Ignore it":\n            jump n_hub' in out


def test_item_take_and_remove():
    out = _out()
    assert '$ inventory.append("flask")' in out
    assert '$ if "flask" in inventory: inventory.remove("flask")' in out


def test_set_and_clear_flag():
    out = _out()
    assert "$ told_truth = True" in out
    assert "$ apologized = True" in out
    assert "$ apologized = False" in out


# --- every condition form renders ---------------------------------------------

def test_condition_item():
    assert '"Drink" if "flask" in inventory:' in _out()


def test_condition_not_wrapping_bare_flag():
    assert '"Confess" if not (told_truth):' in _out()


def test_condition_all_with_var_literals():
    assert '"Push your luck" if (mood > 0) and (patience > 0):' in _out()


def test_condition_any_with_flag_and_var():
    assert '"Make peace" if (apologized) or (mood > 2):' in _out()


def test_condition_var_vs_var():
    assert '"Stand tall" if mood > patience:' in _out()


# --- backgrounds + sprite staging ---------------------------------------------

def _staged_ir():
    return {
        "version": "0.1", "genre": "visual_novel",
        "characters": [
            {"id": "al", "name": "Al", "sprite": "al.png"},
            {"id": "bo", "name": "Bo", "sprite": "bo.png"},
            {"id": "cy", "name": "Cy"},  # no sprite
        ],
        "backgrounds": [{"id": "bg_room", "image_file": "room.png"}],
        "start": {"node": "n1"},
        "nodes": [
            {"id": "n1", "location": "bg_room", "lines": [
                {"speaker": "al", "text": "hi"},
                {"speaker": None, "text": "narr"},
                {"speaker": "bo", "text": "yo"},
                {"speaker": "al", "text": "again"},  # duplicate speaker
                {"speaker": "cy", "text": "no sprite"},
            ], "end": {"type": "return"}},
            {"id": "n2", "lines": [{"speaker": "al", "text": "x"}],  # no location
             "end": {"type": "return"}},
        ],
    }


def test_image_declarations_emitted():
    out = compile_vn(_staged_ir())
    assert 'image bg_room = "images/room.png"' in out
    # character sprites are ATL blocks with a baked zoom so they sit at stage height;
    # single-sprite characters declare just their neutral expression.
    assert 'image char_al_neutral:\n    "images/al.png"\n    zoom 0.55' in out
    assert 'image char_bo_neutral:\n    "images/bo.png"\n    zoom 0.55' in out
    assert "char_cy" not in out  # no sprite, no decl


def test_speaker_highlighting_dims_non_speakers():
    out = compile_vn(_staged_ir())
    assert "transform speaking:\n    alpha 1.0" in out
    assert "transform not_speaking:\n    alpha 0.5" in out
    n1 = out.split("label n1:")[1].split("label n2:")[0]
    # al speaks first with bo also on stage -> al brightened, bo dimmed, before al's line
    assert "show char_al_neutral as char_al at stage(0.3333), speaking" in n1
    assert "show char_bo_neutral as char_bo at stage(0.6667), not_speaking" in n1


def test_no_highlight_with_single_speaker():
    out = compile_vn(_staged_ir())
    # n2 has only al speaking -> nothing to dim, no re-stage on the line
    n2 = out.split("label n2:")[1]
    assert "speaking" not in n2
    assert "not_speaking" not in n2


def test_scene_set_from_node_location():
    out = compile_vn(_staged_ir())
    assert "label n1:\n    scene bg_room\n" in out


def test_all_speakers_shown_up_front_once():
    out = compile_vn(_staged_ir())
    n1 = out.split("label n1:")[1].split("label n2:")[0]
    # preamble = staging before the first spoken line: each sprite-speaker shown once, up front
    preamble = n1.split('al "hi"')[0]
    assert preamble.count("show char_al_neutral as char_al at stage(0.3333)\n") == 1
    assert preamble.count("show char_bo_neutral as char_bo at stage(0.6667)\n") == 1
    # cy has no sprite -> never shown anywhere in the node
    assert "show char_cy" not in n1


def test_sprites_spread_so_all_visible():
    out = compile_vn(_staged_ir())
    n1 = out.split("label n1:")[1].split("label n2:")[0]
    # two visible speakers (al, bo) -> thirds, distinct positions, none overlapping
    assert "show char_al_neutral as char_al at stage(0.3333)" in n1
    assert "show char_bo_neutral as char_bo at stage(0.6667)" in n1
    # n2 has a single sprite speaker -> centred
    n2 = out.split("label n2:")[1]
    assert "show char_al_neutral as char_al at stage(0.5)" in n2


def test_sprite_position_is_deterministic():
    out1 = compile_vn(_staged_ir())
    out2 = compile_vn(_staged_ir())
    assert out1 == out2
    assert "transform stage(x):" in out1


def test_unknown_location_clears_stage_without_a_background():
    # An undeclared/absent location must NOT name a background, but must still emit a bare `scene`
    # so sprites from the previous node don't linger into this one.
    ir = _staged_ir()
    ir["nodes"][0]["location"] = "bg_ghost"  # not a declared background
    out = compile_vn(ir)
    n1 = out.split("label n1:")[1].split("label n2:")[0]
    assert "scene bg_ghost" not in n1     # the bogus id never reaches the script
    assert "\n    scene\n" in n1          # but the stage is reset to clear lingering sprites
    n2 = out.split("label n2:")[1]
    assert "\n    scene\n" in n2           # n2 had no location at all -> still reset


def test_background_carries_forward_to_untagged_node():
    # n1 sets bg_room; n2 has no location -> it should continue the same background, not blank out.
    out = compile_vn(_staged_ir())
    n2 = out.split("label n2:")[1]
    assert "\n    scene bg_room\n" in n2
