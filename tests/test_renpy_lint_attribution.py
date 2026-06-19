import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy import _script
from renpy._script import node_line_ranges, _node_for_line, run_final_lint
from renpy._pnc_script import pnc_line_ranges, _room_screen, _room_driver, _hotspot_logic_block


_SCRIPT = "\n".join([
    "label start:",          # 1
    "    jump scene_1",       # 2
    "",                       # 3
    "label scene_1:",         # 4
    '    a "hi"',             # 5
    "    jump scene_2",       # 6
    "",                       # 7
    "label scene_2:",         # 8
    '    b "bye"',            # 9
])


def test_node_line_ranges_and_lookup():
    ranges = node_line_ranges(_SCRIPT, ["scene_1", "scene_2"])
    assert _node_for_line(5, ranges) == "scene_1"
    assert _node_for_line(6, ranges) == "scene_1"
    assert _node_for_line(9, ranges) == "scene_2"
    # `start` is not in node_ids → not attributed.
    assert _node_for_line(2, ranges) is None


def test_run_final_lint_prefixes_node_id(monkeypatch):
    monkeypatch.setattr(_script, "_run_renpy_lint",
                        lambda *a, **k: 'File "game/script.rpy", line 9: unterminated string')
    ranges = node_line_ranges(_SCRIPT, ["scene_1", "scene_2"])

    out = run_final_lint("out", "sdk", node_ranges=ranges)
    assert out["error_count"] == 1
    assert out["errors"][0].startswith("[scene_2] ")


def test_run_final_lint_unattributed_without_ranges(monkeypatch):
    monkeypatch.setattr(_script, "_run_renpy_lint",
                        lambda *a, **k: 'File "game/script.rpy", line 9: oops')
    out = run_final_lint("out", "sdk")
    assert not out["errors"][0].startswith("[")


# --- point-and-click (rooms) attribution -------------------------------------

def _pnc_script():
    """Stitch a tiny rooms script from the real PNC helpers so the test tracks the actual
    label/screen conventions, not a hand-copied guess at them."""
    room = {"bg": "bg_cell", "hotspots": [
        {"id": "door", "rect": [0, 0, 10, 10], "label": "Door",
         "logic": 'add "larva_kite"\n    jump room_hall'}]}
    parts = [_room_screen("room_cell", room), "label start:", "    jump room_cell",
             _room_driver("room_cell", room), _hotspot_logic_block("room_cell", room["hotspots"][0]),
             "label talk_warden:", '    w "Hello."']
    return "\n".join(parts)


def test_pnc_line_ranges_attributes_room_and_node():
    script = _pnc_script()
    ranges = pnc_line_ranges(script, ["room_cell", "room_hall"], ["talk_warden"])
    # The `add "larva_kite"` line lives in the room's hotspot logic block → room_cell.
    add_line = next(i for i, ln in enumerate(script.split("\n"), 1)
                    if 'add "larva_kite"' in ln)
    assert _node_for_line(add_line, ranges) == "room_cell"
    # The dialogue line maps to the node.
    talk_line = next(i for i, ln in enumerate(script.split("\n"), 1) if '"Hello."' in ln)
    assert _node_for_line(talk_line, ranges) == "talk_warden"


def test_pnc_line_ranges_longest_rid_wins():
    # A rid that is a prefix of another must not swallow the longer one's hotspot labels.
    script = "\n".join([
        _hotspot_logic_block("room_a", {"id": "x", "logic": "return"}),
        _hotspot_logic_block("room_a_b", {"id": "y", "logic": "return"}),
    ])
    ranges = pnc_line_ranges(script, ["room_a", "room_a_b"], [])
    y_line = next(i for i, ln in enumerate(script.split("\n"), 1) if "hs_room_a_b_y" in ln)
    assert _node_for_line(y_line, ranges) == "room_a_b"
