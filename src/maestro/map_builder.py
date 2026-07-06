"""Deterministic walkable-map rasterizer — the model plans, code builds.

The LLM authors a LAYOUT (features on a coarse 3x3 region grid + connections); this module
stamps footprint templates, carves roads between them, and emits the `tiles` grid the godot
presenter already renders. Connectivity is guaranteed by construction — a zone built here can
never be a mud-box, strand a hotspot, or need a reachability fix loop.

Layout shape (validated by world.v_layout):
  {"size": "small" | "medium" | "large",
   "terrain": {"open": "<theme>", "blocked": "<theme>"},
   "features": [{"id": "f_shop", "kind": "building", "at": "southeast",
                 "theme": "timber shopfront", "label": "shop"}],
   "connections": [{"from": "f_gate", "to": "f_fountain"}],
   "exits": [{"edge": "south", "id": "x_south"}]}

`anchors` in the result maps every feature/exit id to the OPEN cell in front of it — where
interactables, spawns, and move-arrivals land.
"""

import hashlib
import random
from typing import Dict, List, Optional, Tuple

SIZES = {"small": (12, 9), "medium": (16, 12), "large": (20, 14)}

REGIONS = ("northwest", "north", "northeast", "west", "center", "east",
           "southwest", "south", "southeast")

FEATURE_KINDS = ("building", "fountain", "camp", "market_stall", "rock_outcrop",
                 "tree_clump", "clearing", "gate")

_EDGES = ("north", "south", "east", "west")

# Footprints: '#' blocked (feature theme), '.' forced-open ground, 'A' the anchor (open cell
# the feature is used FROM — a door front, a fountain edge). Templates stay small; stamps are
# clamped inside the border.
_FOOTPRINTS = {
    "building":     ["###",
                     "###",
                     ".A."],
    "market_stall": ["##",
                     "A."],
    "fountain":     [".#.",
                     "#.#",
                     "A#."],
    "camp":         [".#.",
                     "A.."],
    "rock_outcrop": ["##.",
                     ".##",
                     "A.."],
    "tree_clump":   ["#.#",
                     ".#.",
                     "A#."],
    "clearing":     ["...",
                     ".A.",
                     "..."],
    "gate":         ["A"],
}


def _seed_for(zone_id: str) -> int:
    return int(hashlib.sha256(zone_id.encode()).hexdigest()[:8], 16)


def _region_center(region: str, w: int, h: int) -> Tuple[int, int]:
    col = {"west": 0, "center": 1, "east": 2}
    row = {"north": 0, "center": 1, "south": 2}
    cx, cy = 1, 1
    for name, c in col.items():
        if name in region:
            cx = c
    for name, r in row.items():
        if name in region:
            cy = r
    # region interiors, one cell in from the border ring
    xs = [1 + (w - 2) // 6, w // 2, w - 2 - (w - 2) // 6]
    ys = [1 + (h - 2) // 6, h // 2, h - 2 - (h - 2) // 6]
    return xs[cx], ys[cy]


def _edge_cell(edge: str, w: int, h: int, rng: random.Random) -> Tuple[int, int]:
    if edge == "north":
        return rng.randrange(2, w - 2), 0
    if edge == "south":
        return rng.randrange(2, w - 2), h - 1
    if edge == "west":
        return 0, rng.randrange(2, h - 2)
    return w - 1, rng.randrange(2, h - 2)


def build_tiles(zone_id: str, layout: Dict) -> Dict:
    """Rasterize a layout → {"rows", "legend", "anchors"}. Deterministic per zone_id."""
    rng = random.Random(_seed_for(zone_id))
    w, h = SIZES.get(layout.get("size", "medium"), SIZES["medium"])
    terrain = layout.get("terrain") or {}
    open_theme = terrain.get("open") or "grass"
    blocked_theme = terrain.get("blocked") or "dense brush"

    OPEN, PATH, WALL = ".", ",", "#"
    grid = [[OPEN] * w for _ in range(h)]

    # Irregular border: the blocked ring with occasional double-thick bites.
    for x in range(w):
        grid[0][x] = grid[h - 1][x] = WALL
    for y in range(h):
        grid[y][0] = grid[y][w - 1] = WALL
    for _ in range((w + h) // 4):
        x, y = rng.randrange(1, w - 1), rng.choice([1, h - 2])
        grid[y][x] = WALL
        x, y = rng.choice([1, w - 2]), rng.randrange(1, h - 1)
        grid[y][x] = WALL

    anchors: Dict[str, Tuple[int, int]] = {}
    footprints: Dict[str, Dict] = {}
    footprint_cells: set = set()   # every stamped-solid cell, so roads route AROUND them not through
    feature_theme: Dict[str, str] = {}
    legend_extra: Dict[str, Dict] = {}
    next_char = iter("BCDEFGHIJKLMNOPQRSUVWXYZ")

    def stamp(feat: Dict) -> None:
        kind = feat.get("kind")
        fp = _FOOTPRINTS.get(kind)
        if fp is None:
            return
        fw, fh = len(fp[0]), len(fp)
        cx, cy = _region_center(feat.get("at", "center"), w, h)
        x0 = max(1, min(w - 1 - fw, cx - fw // 2))
        y0 = max(1, min(h - 1 - fh, cy - fh // 2))
        ch = None
        theme = feat.get("theme")
        if theme:
            ch = next(next_char)
            legend_extra[ch] = {"role": "blocked", "theme": theme}
        solid = False
        for dy, row in enumerate(fp):
            for dx, c in enumerate(row):
                gx, gy = x0 + dx, y0 + dy
                if c == "#":
                    grid[gy][gx] = ch or WALL
                    footprint_cells.add((gx, gy))
                    solid = True
                elif c in (".", "A"):
                    grid[gy][gx] = OPEN
                if c == "A":
                    anchors[feat["id"]] = (gx, gy)
        anchors.setdefault(feat["id"], (x0, min(h - 2, y0 + fh)))
        feature_theme[feat["id"]] = theme or kind
        if solid:
            footprints[feat["id"]] = {"x": x0, "y": y0, "w": fw, "h": fh, "kind": kind,
                                      "label": feat.get("label") or theme or kind}

    features = [f for f in layout.get("features") or [] if isinstance(f, dict) and f.get("id")]
    for feat in features:
        stamp(feat)

    def _inward(edge, x, y):
        if edge == "north":
            return x, 1
        if edge == "south":
            return x, h - 2
        if edge == "west":
            return 1, y
        return w - 2, y

    for ex in layout.get("exits") or []:
        edge = ex.get("edge")
        if edge not in _EDGES or not ex.get("id"):
            continue
        # Re-roll the edge cell so its inward doorstep doesn't punch a hole through a footprint
        # (an exit corridor cut straight through a stamped building).
        x, y = _edge_cell(edge, w, h, rng)
        for _ in range(10):
            if _inward(edge, x, y) not in footprint_cells:
                break
            x, y = _edge_cell(edge, w, h, rng)
        grid[y][x] = OPEN
        ix, iy = _inward(edge, x, y)
        grid[iy][ix] = OPEN
        anchors[ex["id"]] = (x, y)

    # Roads: carve ',' between every connection's endpoints; default chain links every anchor so the
    # zone is one walk even with no connections authored. A BFS over the open interior (footprints
    # are obstacles, endpoints excepted) routes the road AROUND buildings instead of an L-walk
    # straight through them — open space minus small footprint islands is connected and the anchors
    # sit on a footprint's open side, so a route always exists (connectivity preserved).
    def carve(a: Tuple[int, int], b: Tuple[int, int]) -> None:
        from collections import deque
        if a == b:
            return
        prev = {a: None}
        q = deque([a])
        while q:
            cur = q.popleft()
            if cur == b:
                break
            cx, cy = cur
            for nb in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                nx, ny = nb
                if not (0 <= nx < w and 0 <= ny < h) or nb in prev:
                    continue
                if nb != b and ((nx in (0, w - 1) or ny in (0, h - 1)) or nb in footprint_cells):
                    continue  # don't route along the border ring or through a footprint
                prev[nb] = cur
                q.append(nb)
        if b not in prev:
            return  # no route (open interior fully walled off) — leave the anchors as they are
        node = b
        while node is not None:
            x, y = node
            if not (grid[y][x] != OPEN and (x in (0, w - 1) or y in (0, h - 1))):
                grid[y][x] = PATH
            node = prev[node]

    pairs: List[Tuple[str, str]] = []
    for con in layout.get("connections") or []:
        a, b = con.get("from"), con.get("to")
        if a in anchors and b in anchors:
            pairs.append((a, b))
    if not pairs and len(anchors) >= 2:
        ids = list(anchors)
        pairs = list(zip(ids, ids[1:]))
    else:
        # every anchor not touched by an authored connection still joins the network
        touched = {i for p in pairs for i in p}
        ids = [i for i in anchors if i not in touched]
        base = pairs[0][0] if pairs else None
        for i in ids:
            if base:
                pairs.append((base, i))
    for a, b in pairs:
        carve(anchors[a], anchors[b])

    # Scatter obstacles as ATOMIC clumps on plain open ground, never on paths/anchors/footprints.
    # The whole clump is stamped as a unit (a single roll over a 2x2 stencil) so a "clump" can never
    # collapse to a lone 1-tile pillar the way independent per-cell rolls did.
    anchor_cells = set(anchors.values())
    for _ in range(max(2, (w * h) // 40)):
        x, y = rng.randrange(2, w - 2), rng.randrange(2, h - 2)
        cells = [(x + dx, y + dy) for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1))]
        open_cells = [(gx, gy) for gx, gy in cells
                      if 0 <= gx < w and 0 <= gy < h and grid[gy][gx] == OPEN
                      and (gx, gy) not in anchor_cells and (gx, gy) not in footprint_cells]
        if len(open_cells) >= 2 and rng.random() < 0.7:
            for gx, gy in open_cells:
                grid[gy][gx] = WALL

    rows = ["".join(r) for r in grid]
    legend = {
        ".": {"role": "open", "theme": open_theme},
        ",": {"role": "open", "theme": f"worn {open_theme} path"},
        "#": {"role": "blocked", "theme": blocked_theme},
        **legend_extra,
    }
    return {"rows": rows, "legend": legend,
            "anchors": {k: {"x": v[0], "y": v[1]} for k, v in anchors.items()},
            "footprints": footprints}


def open_cells(tiles: Dict) -> set:
    """The set of walkable (x, y) cells a tiles grid declares."""
    rows = (tiles or {}).get("rows") or []
    legend = (tiles or {}).get("legend") or {}
    default_open = {".", ","}
    cells = set()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            entry = legend.get(ch)
            role = entry.get("role") if isinstance(entry, dict) else None
            if role == "open" or (role is None and ch in default_open):
                cells.add((x, y))
    return cells


def snap_to_open(tiles: Dict, x: int, y: int, occupied=()) -> Optional[Tuple[int, int]]:
    """Nearest open cell to (x, y) — deterministic, so a hotspot dropped on a wall is moved,
    never bounced to an LLM fixer (observed: 6 failed fixes then a parked build over one
    blocked cell). `occupied` cells (other hotspots) are avoided the same way (observed: two
    takes on one tile parked a build); if every open cell is taken, nearest open wins."""
    cells = open_cells(tiles)
    if not cells:
        return None
    free = cells - set(occupied) or cells
    if (x, y) in free:
        return (x, y)
    return min(free, key=lambda c: (abs(c[0] - x) + abs(c[1] - y), c[1], c[0]))


def v_layout(layout: Dict) -> Optional[str]:
    if not isinstance(layout, dict):
        return "layout must be an object {size, terrain, features, connections, exits}"
    if layout.get("size") not in SIZES:
        return f"layout.size must be one of {sorted(SIZES)}"
    feats = layout.get("features")
    if not isinstance(feats, list) or not feats:
        return "layout.features must be a non-empty list of {id, kind, at, theme?, label?}"
    ids = set()
    for i, f in enumerate(feats):
        if not isinstance(f, dict) or not f.get("id"):
            return f"layout.features[{i}] needs an 'id'"
        if f.get("kind") not in FEATURE_KINDS:
            return (f"layout.features[{i}].kind {f.get('kind')!r} must be one of "
                    f"{sorted(FEATURE_KINDS)}")
        if f.get("at") not in REGIONS:
            return (f"layout.features[{i}].at {f.get('at')!r} must be one of {sorted(REGIONS)} "
                    f"(a coarse 3x3 region — the builder places the exact cells)")
        ids.add(f["id"])
    for i, ex in enumerate(layout.get("exits") or []):
        if not isinstance(ex, dict) or ex.get("edge") not in _EDGES or not ex.get("id"):
            return f"layout.exits[{i}] needs an 'id' and an 'edge' in {_EDGES}"
        ids.add(ex["id"])
    for i, c in enumerate(layout.get("connections") or []):
        if not isinstance(c, dict) or c.get("from") not in ids or c.get("to") not in ids:
            return (f"layout.connections[{i}] must reference declared feature/exit ids "
                    f"({sorted(ids)})")
    return None
