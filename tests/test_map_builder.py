"""The walkable-map generators: structure-first per place kind (town roads+parcels, interior
room graph, world_map terrain fill), two-tier objects (mechanical layout.features + the derived
layout.furniture list), and the by-construction invariants — connected, non-overlapping,
anchor-valid, deterministic. Failures print the map via render_ascii so a human can SEE it."""
import sys
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.map_builder import (
    FEATURE_SIZES, build_tiles, open_cells, render_ascii, v_furniture, v_layout)

KINDS = ("town", "interior", "world_map")
SEEDS = ("zone_a", "zone_ruined_pier", "zone_9", "deepwood", "keep_gate_2")


def _layout(kind, size="medium", features=None, exits=None, furniture=None):
    lay = {
        "kind": kind, "size": size,
        "terrain": {"open": "mossy earth", "blocked": "bone-pale cliff"},
        "features": features if features is not None else [
            {"id": "f_hall", "size": "large", "at": "northwest", "label": "hall",
             "theme": "timber hall"},
            {"id": "f_well", "size": "small", "at": "center", "label": "well"},
            {"id": "f_yard", "size": "area", "at": "east", "label": "yard"},
            {"id": "f_post", "size": "spot", "at": "south", "label": "signpost"},
        ],
        "exits": exits if exits is not None else [
            {"id": "x_south", "edge": "south"}, {"id": "x_west", "edge": "west"}],
    }
    if furniture is not None:
        lay["furniture"] = furniture
    return lay


_FURNITURE = [
    {"object": "barrel", "size": "small", "flavor": "rainwater, half full"},
    {"object": "hay cart", "size": "medium"},
    {"object": "crate pile", "size": "small"},
]


def _reachable(built, src):
    opens = open_cells({"rows": built["rows"], "legend": built["legend"]})
    seen = {src} if src in opens else set()
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nb in opens and nb not in seen:
                seen.add(nb)
                dq.append(nb)
    return opens, seen


def _footprint_cells(built):
    cells = {}
    for fid, fp in built["footprints"].items():
        for dx in range(fp["w"]):
            for dy in range(fp["h"]):
                cell = (fp["x"] + dx, fp["y"] + dy)
                yield fid, cell, cells.get(cell)
                cells[cell] = fid


# ── the by-construction invariants, every kind x several seeds ────────────────

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("zone_id", SEEDS)
def test_generator_invariants(kind, zone_id):
    lay = _layout(kind, furniture=_FURNITURE)
    assert v_layout(lay) is None, v_layout(lay)
    built = build_tiles(zone_id, lay)
    debug = render_ascii(built)

    rows = built["rows"]
    w, h = len(rows[0]), len(rows)
    assert all(len(r) == w for r in rows), debug

    # every declared feature/exit id got an anchor
    declared = {f["id"] for f in lay["features"]} | {e["id"] for e in lay["exits"]}
    assert declared <= set(built["anchors"]), debug

    # every anchor is an open cell, and ALL open cells are one connected walk (no sealed
    # pocket a snapped hotspot could land in)
    anchors = {aid: (a["x"], a["y"]) for aid, a in built["anchors"].items()}
    root = next(iter(anchors.values()))
    opens, seen = _reachable(built, root)
    for aid, cell in anchors.items():
        assert cell in opens, f"{aid} anchor blocked\n{debug}"
        assert cell in seen, f"{aid} unreachable\n{debug}"
    assert opens == seen, f"open pocket not sealed\n{debug}"

    # footprints: in bounds, pairwise disjoint, solid on the grid, never under an anchor
    anchor_cells = set(anchors.values())
    for fid, (x, y), clash in _footprint_cells(built):
        assert clash is None, f"{fid} overlaps {clash} at {(x, y)}\n{debug}"
        assert 0 < x < w - 1 and 0 < y < h - 1, f"{fid} out of bounds\n{debug}"
        assert (x, y) not in opens, f"{fid} cell {(x, y)} not solid\n{debug}"
        assert (x, y) not in anchor_cells, f"anchor inside {fid}\n{debug}"

    # exits sit on their declared edge, open
    edge_of = {e["id"]: e["edge"] for e in lay["exits"]}
    for xid, edge in edge_of.items():
        x, y = anchors[xid]
        at = {"north": y == 0, "south": y == h - 1, "west": x == 0, "east": x == w - 1}[edge]
        assert at, f"{xid} not on {edge} edge\n{debug}"

    # deterministic per zone id
    assert build_tiles(zone_id, lay) == built

    # PATH tiles exist (roads / doorways / spine)
    assert any("," in r for r in rows), debug


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("zone_id", SEEDS)
def test_footprint_overlap_impossible_under_crowding(kind, zone_id):
    # four LARGE features all demanding the same region + a full furniture list: the old
    # center-stamp silently clobbered; now placement claims cells, so overlap cannot happen
    feats = [{"id": f"f_{i}", "size": "large", "at": "center", "label": f"keep {i}"}
             for i in range(4)]
    built = build_tiles(zone_id, _layout(kind, size="small", features=feats,
                                         furniture=_FURNITURE))
    debug = render_ascii(built)
    for fid, cell, clash in _footprint_cells(built):
        assert clash is None, f"{fid} overlaps {clash} at {cell}\n{debug}"
    # must-place: every feature still got a valid open anchor
    opens, seen = _reachable(built, next(iter(
        (a["x"], a["y"]) for a in built["anchors"].values())))
    for i in range(4):
        a = built["anchors"].get(f"f_{i}")
        assert a and (a["x"], a["y"]) in seen, f"f_{i} lost\n{debug}"


# ── the furniture tier ────────────────────────────────────────────────────────

def test_furniture_fills_from_list_and_never_moves_features():
    lay_bare = _layout("town")
    lay_full = _layout("town", furniture=_FURNITURE)
    bare = build_tiles("zone_t", lay_bare)
    full = build_tiles("zone_t", lay_full)
    # furniture placed last: the structural pass is byte-identical, so features + their
    # anchors are exactly where they were before furnishing
    for aid in bare["anchors"]:
        assert full["anchors"][aid] == bare["anchors"][aid]
    for fid in bare["footprints"]:
        assert full["footprints"][fid] == bare["footprints"][fid]
    placed = [fid for fid in full["footprints"] if fid.startswith("fn_")]
    assert placed, render_ascii(full)
    labels = {full["footprints"][fid]["label"] for fid in placed}
    assert labels <= {f["object"] for f in _FURNITURE}
    # density-controlled: each object lands at most twice
    for f in _FURNITURE:
        assert sum(1 for fid in placed
                   if full["footprints"][fid]["label"] == f["object"]) <= 2
    # every placement is anchored (a doorstep for the flavor hotspot)
    assert all(fid in full["anchors"] for fid in placed), render_ascii(full)


# ── per-kind structure ────────────────────────────────────────────────────────

def test_town_buildings_face_the_road():
    built = build_tiles("zone_town", _layout("town", furniture=_FURNITURE))
    rows, debug = built["rows"], render_ascii(built)
    road = {(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == ","}
    assert road, debug
    for fid, fp in built["footprints"].items():
        a = built["anchors"][fid]
        # the doorstep touches the footprint...
        assert (fp["x"] - 1 <= a["x"] <= fp["x"] + fp["w"]
                and fp["y"] - 1 <= a["y"] <= fp["y"] + fp["h"]), f"{fid} door far\n{debug}"


def test_interior_has_rooms_and_doorways():
    built = build_tiles("zone_hall", _layout("interior", size="large",
                                             furniture=_FURNITURE))
    rows, debug = built["rows"], render_ascii(built)
    w, h = len(rows[0]), len(rows)
    inner_walls = [(x, y) for y in range(1, h - 1) for x in range(1, w - 1)
                   if rows[y][x] == "#"]
    assert inner_walls, f"no room walls\n{debug}"
    assert any("," in r for r in rows), f"no doorways\n{debug}"


def test_wild_grows_blocked_masses():
    built = build_tiles("zone_deepwood", _layout("world_map", size="large",
                                                 furniture=_FURNITURE))
    rows, debug = built["rows"], render_ascii(built)
    w, h = len(rows[0]), len(rows)
    inner_blocked = sum(1 for y in range(2, h - 2) for x in range(2, w - 2)
                        if rows[y][x] == "#")
    assert inner_blocked >= (w * h) // 20, f"wild reads as an empty field\n{debug}"


def test_open_features_reserve_ground_not_solids():
    built = build_tiles("zone_g", _layout("world_map"))
    assert "f_yard" not in built["footprints"] and "f_post" not in built["footprints"]
    assert "f_yard" in built["anchors"] and "f_post" in built["anchors"]


# ── the debug renderer ────────────────────────────────────────────────────────

def test_render_ascii_shows_map_anchors_and_legend():
    built = build_tiles("zone_r", _layout("town", furniture=_FURNITURE))
    out = render_ascii(built)
    lines = out.splitlines()
    assert lines[0] == built["rows"][0]           # the map itself, row for row
    assert any(l.startswith("anchors: ") and "f_hall" in l for l in lines)
    assert any("legend" in l and "mossy earth" in l for l in lines)


# ── the validators ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("layout, frag", [
    ({"size": "huge"}, "layout.size"),
    ({"size": "small", "features": []}, "non-empty"),
    # the old kind-keyed feature vocabulary is gone — a feature declares a size
    ({"size": "small", "features": [
        {"id": "f", "kind": "building", "at": "center", "label": "x"}]}, "size"),
    ({"size": "small", "features": [
        {"id": "f", "size": "gigantic", "at": "center", "label": "x"}]}, "size"),
    ({"size": "small", "features": [
        {"id": "f", "size": "small", "at": "middle", "label": "x"}]}, "at"),
    ({"size": "small", "features": [
        {"id": "f", "size": "small", "at": "center"}]}, "label"),
    ({"size": "small", "features": [
        {"id": "f", "size": "small", "at": "center", "label": "x"}],
      "exits": [{"id": "x_n"}]}, "edge"),
    ({"size": "small", "features": [
        {"id": "f", "size": "small", "at": "center", "label": "x"}],
      "furniture": [{"object": "barrel"}]}, "furniture[0].size"),
], ids=["bad_size", "no_features", "old_kind_vocabulary", "bad_feature_size", "bad_region",
        "missing_label", "bad_exit", "bad_furniture"])
def test_v_layout_rejects(layout, frag):
    err = v_layout(layout)
    assert err and frag in err, err


@pytest.mark.parametrize("furniture, frag", [
    ([], "non-empty"),
    ([{"size": "small"}], "object"),
    ([{"object": "barrel", "size": "vast"}], "size"),
    ([{"object": "barrel", "size": "small", "flavor": 3}], "flavor"),
    ([{"object": f"o{i}", "size": "small"} for i in range(13)], "at most 12"),
], ids=["empty", "no_object", "bad_size", "bad_flavor", "too_many"])
def test_v_furniture_rejects(furniture, frag):
    err = v_furniture(furniture)
    assert err and frag in err, err


def test_v_layout_accepts_every_feature_size():
    feats = [{"id": f"f_{s}", "size": s, "at": "center", "label": s} for s in FEATURE_SIZES]
    assert v_layout({"size": "medium", "features": feats}) is None
