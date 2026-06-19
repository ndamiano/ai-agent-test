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
