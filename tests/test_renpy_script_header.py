import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy._script import _stitch_script


def _define_lines(script: str):
    return [ln for ln in script.splitlines() if ln.startswith("define ") and "Character(" in ln]


def test_character_name_with_quotes_emits_valid_python():
    # A display name with a nickname in quotes must not break the Character() literal.
    bible = {"characters": [{"id": "jinx_park", "name": 'Corporal Jun "Jinx" Park', "color": "#c8c8ff"}]}
    script = _stitch_script(bible, {"backgrounds": [], "cgs": []}, {}, [])

    line = next(ln for ln in _define_lines(script) if ln.startswith("define jinx_park"))
    # The body after `define <id> = ` must parse as a Python expression.
    expr = line.split("=", 1)[1].strip()
    node = ast.parse(expr, mode="eval")
    # First positional arg is the name string, with the inner quotes preserved.
    assert node.body.args[0].value == 'Corporal Jun "Jinx" Park'


def test_plain_character_name_unchanged():
    bible = {"characters": [{"id": "mara", "name": "Sergeant Mara Kincaid", "color": "#c8ffc8"}]}
    script = _stitch_script(bible, {"backgrounds": [], "cgs": []}, {}, [])
    line = next(ln for ln in _define_lines(script) if ln.startswith("define mara"))
    assert 'Character("Sergeant Mara Kincaid", color="#c8ffc8")' in line
