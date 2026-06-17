import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.checks import node_view


def test_node_view_edges_reachability_lines():
    artifact = {"node_scripts": {
        "node_ids": ["s1", "s2", "s3"],
        "scripts": {
            "s1": 'label s1:\n    a "hi"\n    jump s2',
            "s2": 'label s2:\n    a "x"\n    a "y"',
            "s3": 'label s3:\n    a "orphan line"',   # nothing jumps here
        },
    }}
    view = node_view(artifact)

    assert view["node_ids"] == ["s1", "s2", "s3"]
    assert view["edges"]["s1"] == ["s2"]
    assert view["edges"]["s2"] == []
    assert set(view["reachable"]) == {"s1", "s2"}
    assert view["unreachable"] == ["s3"]
    assert view["line_counts"]["s2"] == 2


def test_node_view_empty():
    assert node_view({})["node_ids"] == []
