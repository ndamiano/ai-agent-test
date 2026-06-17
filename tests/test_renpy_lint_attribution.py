import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy import _script
from renpy._script import node_line_ranges, _node_for_line, run_final_lint


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
