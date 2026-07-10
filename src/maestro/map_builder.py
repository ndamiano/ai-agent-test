"""Deterministic walkable-map generators — the model plans, code builds.

Structure first, keyed on the place's `kind` (no one-size stamp-and-scatter):
  town      — roads first (every exit runs to a central plaza), then buildings on parcels
              OFF the roads, each with a doorstep facing the road
  interior  — a room graph: BSP rooms behind real 1-tile walls, one doorway per split,
              objects placed against the walls inside rooms
  world_map — terrain fill: organic borders and blocked masses grown around a carved path
              spine that links every exit and feature

A zone's objects are two tiers, never free-form:
  layout.features  — MECHANICAL: declared by the model because an interactable stands there
                     (must-place, precise, footprint scaled by the declared `size`)
  layout.furniture — VERISIMILITUDE: the derived per-place list ({object, size, flavor?});
                     code fills the map from it to density, placed LAST so re-furnishing a
                     zone never moves a feature or its anchor

Invariants by construction: footprints never overlap (every placement claims free cells and
is reverted if it would cut the walk graph — no center-stamp clobber), every anchor is an
open doorstep cell, every open cell is reachable from the first anchor (unreachable pockets
are sealed shut), and the whole build is deterministic per zone id.

Layout shape (validated by v_layout):
  {"size": "small" | "medium" | "large",
   "kind": "town" | "interior" | "world_map",       # stamped from the place by write_place
   "terrain": {"open": "<theme>", "blocked": "<theme>"},
   "features": [{"id": "f_forge", "size": "large", "at": "northwest",
                 "label": "forge", "theme": "soot-black timber forge"}],
   "exits": [{"edge": "south", "id": "x_south"}],
   "furniture": [{"object": "anvil", "size": "small", "flavor": "..."}]}   # set_furniture

`anchors` in the result maps every feature/exit/furniture id to the OPEN cell in front of it —
where interactables, spawns, and move-arrivals land.
"""

import hashlib
import random
import re
from collections import deque
from typing import Dict, Iterable, List, Optional, Tuple

SIZES = {"small": (12, 9), "medium": (16, 12), "large": (20, 14)}

REGIONS = ("northwest", "north", "northeast", "west", "center", "east",
           "southwest", "south", "southeast")

FEATURE_SIZES = ("spot", "small", "medium", "large", "area")
FURNITURE_SIZES = ("small", "medium", "large")
_SOLID_DIMS = {"small": (1, 1), "medium": (2, 2), "large": (3, 2)}

_EDGES = ("north", "south", "east", "west")

OPEN, PATH, WALL = ".", ",", "#"


def _seed_for(zone_id: str) -> int:
    return int(hashlib.sha256(zone_id.encode()).hexdigest()[:8], 16)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "obj"


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
    xs = [1 + (w - 2) // 6, w // 2, w - 2 - (w - 2) // 6]
    ys = [1 + (h - 2) // 6, h // 2, h - 2 - (h - 2) // 6]
    return xs[cx], ys[cy]


def _inward(edge: str, x: int, y: int, w: int, h: int) -> Tuple[int, int]:
    if edge == "north":
        return x, 1
    if edge == "south":
        return x, h - 2
    if edge == "west":
        return 1, y
    return w - 2, y


def _flood(free: set, src: Tuple[int, int]) -> set:
    seen = {src} if src in free else set()
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nb in free and nb not in seen:
                seen.add(nb)
                dq.append(nb)
    return seen


class _Map:
    """The under-construction grid + the placement/connectivity machinery all three
    generators share. Solids only ever land on free claimed cells and are reverted if they
    would cut any anchor off — overlap and stranding are impossible, not just unlikely."""

    def __init__(self, zone_id: str, layout: Dict):
        self.rng = random.Random(_seed_for(zone_id))
        self.w, self.h = SIZES.get(layout.get("size", "medium"), SIZES["medium"])
        self.grid = [[OPEN] * self.w for _ in range(self.h)]
        self.anchors: Dict[str, Tuple[int, int]] = {}
        self.footprints: Dict[str, Dict] = {}
        self._char_theme: Dict[str, str] = {}
        self._theme_char: Dict[str, str] = {}
        self._chars = iter("BCDEFGHIJKLMNOPQRSUVWXYZ0123456789")
        self.reserved: set = set()   # cells a solid may never claim (roads, doorsteps, throats)
        self.musts: List[Tuple[int, int]] = []   # non-anchor cells that must stay open+linked

    # ── cells ──────────────────────────────────────────────────────────────
    def interior_cell(self, x: int, y: int) -> bool:
        return 1 <= x <= self.w - 2 and 1 <= y <= self.h - 2

    def is_open(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h and self.grid[y][x] in (OPEN, PATH)

    def placeable(self, x: int, y: int) -> bool:
        return (self.interior_cell(x, y) and self.grid[y][x] == OPEN
                and (x, y) not in self.reserved)

    def open_set(self) -> set:
        return {(x, y) for y in range(self.h) for x in range(self.w)
                if self.grid[y][x] in (OPEN, PATH)}

    def char_for(self, theme: str) -> str:
        if theme not in self._theme_char:
            ch = next(self._chars, WALL)
            self._theme_char[theme] = ch
            if ch != WALL:
                self._char_theme[ch] = theme
        return self._theme_char[theme]

    def ring(self) -> None:
        for x in range(self.w):
            self.grid[0][x] = self.grid[self.h - 1][x] = WALL
        for y in range(self.h):
            self.grid[y][0] = self.grid[y][self.w - 1] = WALL

    def bite_corners(self) -> None:
        """Staircase bites out of 1-2 corners — a non-rectangular usable area that is
        structure, not noise."""
        corners = [(1, 1, 1, 1), (self.w - 2, 1, -1, 1),
                   (1, self.h - 2, 1, -1), (self.w - 2, self.h - 2, -1, -1)]
        for cx, cy, sx, sy in self.rng.sample(corners, self.rng.randint(1, 2)):
            depth = self.rng.randint(2, 3)
            for i in range(depth):
                for j in range(depth - i):
                    self.grid[cy + j * sy][cx + i * sx] = WALL

    # ── connectivity ───────────────────────────────────────────────────────
    def connected(self) -> bool:
        musts = list(self.anchors.values()) + self.musts
        if len(musts) <= 1:
            return True
        free = self.open_set()
        if any(m not in free for m in musts):
            return False
        return set(musts) <= _flood(free, musts[0])

    # ── placement ──────────────────────────────────────────────────────────
    def try_solid(self, fid: str, dims: Tuple[int, int], candidates: Iterable,
                  *, theme: str, label: str, kind: str) -> Optional[Tuple[int, int]]:
        """Stamp a fw x fh solid at the first candidate (x0, y0, door) whose cells are all
        free and whose placement keeps every anchor mutually reachable. Returns the doorstep
        (= the anchor) or None."""
        fw, fh = dims
        taken = set(self.anchors.values())
        for x0, y0, door in candidates:
            cells = [(x0 + dx, y0 + dy) for dy in range(fh) for dx in range(fw)]
            if not all(self.placeable(x, y) for x, y in cells):
                continue
            if door in cells or not self.is_open(*door) or door in taken:
                continue
            ch = self.char_for(theme)
            for x, y in cells:
                self.grid[y][x] = ch
            self.anchors[fid] = door
            if not self.connected():
                for x, y in cells:
                    self.grid[y][x] = OPEN
                del self.anchors[fid]
                continue
            self.reserved.add(door)
            self.footprints[fid] = {"x": x0, "y": y0, "w": fw, "h": fh,
                                    "kind": kind, "label": label}
            return door
        return None

    def try_open(self, fid: str, dims: Tuple[int, int],
                 positions: Iterable) -> Optional[Tuple[int, int]]:
        """Claim a forced-open patch (a plaza, a glade, a single gate tile): no solid is
        stamped, the cells are reserved so nothing lands in them later. Anchor = center."""
        fw, fh = dims
        for x0, y0 in positions:
            cells = [(x0 + dx, y0 + dy) for dy in range(fh) for dx in range(fw)]
            if not all(self.placeable(x, y) for x, y in cells):
                continue
            self.anchors[fid] = (x0 + fw // 2, y0 + fh // 2)
            if not self.connected():
                del self.anchors[fid]
                continue
            self.reserved.update(cells)
            return self.anchors[fid]
        return None

    def anchor_fallback(self, fid: str, prefer: Tuple[int, int]) -> Optional[Tuple[int, int]]:
        """A must-place feature that fit nowhere still gets a valid open anchor near its
        region — the map never fails to build."""
        taken = set(self.anchors.values())
        opens = [c for c in self.open_set()
                 if self.interior_cell(*c) and c not in taken]
        for c in sorted(opens, key=lambda c: (abs(c[0] - prefer[0]) + abs(c[1] - prefer[1]),
                                              c[1], c[0])):
            self.anchors[fid] = c
            if self.connected():
                self.reserved.add(c)
                return c
            del self.anchors[fid]
        return None

    def place_exit(self, xid: str, edge: str) -> Optional[Tuple[int, int]]:
        spans = {"north": [(x, 0) for x in range(2, self.w - 2)],
                 "south": [(x, self.h - 1) for x in range(2, self.w - 2)],
                 "west": [(0, y) for y in range(2, self.h - 2)],
                 "east": [(self.w - 1, y) for y in range(2, self.h - 2)]}[edge]
        self.rng.shuffle(spans)
        for x, y in spans:
            ix, iy = _inward(edge, x, y, self.w, self.h)
            if self.grid[iy][ix] in (OPEN, PATH) and (ix, iy) not in self.reserved:
                break
        else:
            x, y = spans[len(spans) // 2]
            ix, iy = _inward(edge, x, y, self.w, self.h)
            self.grid[iy][ix] = OPEN
        self.grid[y][x] = OPEN
        self.anchors[xid] = (x, y)
        self.musts.append((ix, iy))
        self.reserved.update({(x, y), (ix, iy)})
        # punch the throat clear of border growth: two cells inward reaches the main field,
        # so an exit can never open into a sealed nook of the wild border
        dx, dy = {"north": (0, 1), "south": (0, -1), "west": (1, 0), "east": (-1, 0)}[edge]
        for step in (1, 2):
            px, py = ix + dx * step, iy + dy * step
            if self.interior_cell(px, py) and self.grid[py][px] not in (OPEN, PATH):
                self.grid[py][px] = OPEN
        return (ix, iy)

    # ── carving ────────────────────────────────────────────────────────────
    def carve_to_net(self, a: Tuple[int, int], net: set) -> None:
        """BFS from `a` over open cells to the nearest net cell; mark the route PATH and add
        it to the net. Seeds the net when it is empty."""
        free = self.open_set()
        if a not in free:
            return
        if not net:
            net.add(a)
            return
        if a in net:
            return
        prev = {a: None}
        dq = deque([a])
        hit = None
        while dq:
            cur = dq.popleft()
            if cur in net:
                hit = cur
                break
            x, y = cur
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in prev or nb not in free:
                    continue
                if nb not in net and not self.interior_cell(*nb):
                    continue   # never route along the border ring
                prev[nb] = cur
                dq.append(nb)
        if hit is None:
            net.add(a)
            return
        node = hit
        while node is not None:
            x, y = node
            if self.interior_cell(x, y) and self.grid[y][x] == OPEN:
                self.grid[y][x] = PATH
            self.reserved.add(node)
            net.add(node)
            node = prev[node]


# ── candidate enumeration ─────────────────────────────────────────────────────
def _positions(m: _Map, dims: Tuple[int, int]) -> List[Tuple[int, int]]:
    fw, fh = dims
    return [(x0, y0) for y0 in range(1, m.h - fh) for x0 in range(1, m.w - fw)]


def _sorted_positions(m: _Map, dims: Tuple[int, int], prefer: Tuple[int, int]) -> List:
    fw, fh = dims
    return sorted(_positions(m, dims),
                  key=lambda p: (abs(p[0] + fw // 2 - prefer[0])
                                 + abs(p[1] + fh // 2 - prefer[1]), p[1], p[0]))


def _door_for(m: _Map, x0: int, y0: int, fw: int, fh: int) -> Optional[Tuple[int, int]]:
    taken = set(m.anchors.values())
    for door in ((x0 + fw // 2, y0 + fh), (x0 + fw // 2, y0 - 1),
                 (x0 + fw, y0 + fh // 2), (x0 - 1, y0 + fh // 2)):
        if m.interior_cell(*door) and m.is_open(*door) and door not in taken:
            return door
    return None


def _scatter_candidates(m: _Map, dims: Tuple[int, int], prefer: Tuple[int, int],
                        near_wall: bool = False) -> List:
    fw, fh = dims

    def touches_wall(x0, y0):
        for x in range(x0 - 1, x0 + fw + 1):
            for y in (y0 - 1, y0 + fh):
                if not m.is_open(x, y):
                    return True
        for y in range(y0, y0 + fh):
            for x in (x0 - 1, x0 + fw):
                if not m.is_open(x, y):
                    return True
        return False

    out = []
    for x0, y0 in _positions(m, dims):
        door = _door_for(m, x0, y0, fw, fh)
        if door is None:
            continue
        dist = abs(x0 + fw // 2 - prefer[0]) + abs(y0 + fh // 2 - prefer[1])
        rank = (0 if touches_wall(x0, y0) else 1) if near_wall else 0
        out.append((rank, dist, y0, x0, door))
    return [(x0, y0, door) for rank, dist, y0, x0, door in sorted(out)]


def _road_candidates(m: _Map, dims: Tuple[int, int], prefer: Tuple[int, int]) -> List:
    """Town parcels: the doorstep sits beside a road cell, the building behind the doorstep —
    every placed building faces the street by construction."""
    fw, fh = dims
    taken = set(m.anchors.values())
    out = []
    for ry in range(m.h):
        for rx in range(m.w):
            if m.grid[ry][rx] != PATH:
                continue
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                door = (rx + dx, ry + dy)
                if (not m.interior_cell(*door) or m.grid[door[1]][door[0]] != OPEN
                        or door in m.reserved or door in taken):
                    continue
                if dx == 0:
                    x0 = door[0] - (fw - 1) // 2
                    y0 = door[1] + 1 if dy > 0 else door[1] - fh
                else:
                    y0 = door[1] - (fh - 1) // 2
                    x0 = door[0] + 1 if dx > 0 else door[0] - fw
                dist = abs(door[0] - prefer[0]) + abs(door[1] - prefer[1])
                out.append((dist, door[1], door[0], x0, y0, door))
    return [(x0, y0, door) for dist, dy_, dx_, x0, y0, door in sorted(out)]


# ── shared feature/furniture stages ───────────────────────────────────────────
def _features_of(layout: Dict) -> List[Dict]:
    return [f for f in layout.get("features") or [] if isinstance(f, dict) and f.get("id")]


def _exits_of(layout: Dict) -> List[Dict]:
    return [ex for ex in layout.get("exits") or []
            if isinstance(ex, dict) and ex.get("id") and ex.get("edge") in _EDGES]


def _place_feature(m: _Map, feat: Dict, solid_cands) -> None:
    prefer = _region_center(feat.get("at", "center"), m.w, m.h)
    size = feat.get("size")
    if size in ("spot", "area"):
        for dims in ([(1, 1)] if size == "spot" else [(3, 3), (2, 2), (1, 1)]):
            if m.try_open(feat["id"], dims, _sorted_positions(m, dims, prefer)):
                return
        m.anchor_fallback(feat["id"], prefer)
        return
    dims = _SOLID_DIMS[size]
    label = feat.get("label") or feat.get("theme") or feat["id"]
    door = m.try_solid(feat["id"], dims, solid_cands(dims, prefer),
                       theme=feat.get("theme") or label, label=label, kind=label)
    if door is None:
        m.anchor_fallback(feat["id"], prefer)


def _furnish(m: _Map, layout: Dict, cands) -> None:
    """Fill the map from the furniture list to density — ambient objects, placed last so a
    re-furnish never moves a feature. Each object lands at most twice; every placement is a
    real footprint, so each distinct object name maps to one generated sprite."""
    entries = [f for f in layout.get("furniture") or []
               if isinstance(f, dict) and f.get("object")]
    if not entries:
        return
    budget = max(3, (m.w * m.h) // 16)
    used = 0
    for round_ in (1, 2):
        for f in entries:
            if used >= budget:
                return
            dims = _SOLID_DIMS.get(f.get("size"), _SOLID_DIMS["small"])
            prefer = (m.rng.randrange(1, m.w - 1), m.rng.randrange(1, m.h - 1))
            obj = f["object"]
            door = m.try_solid(f"fn_{_slug(obj)}_{round_}", dims, cands(dims, prefer),
                               theme=obj, label=obj, kind=obj)
            if door:
                used += dims[0] * dims[1]


def _link(m: _Map) -> None:
    net = {(x, y) for y in range(m.h) for x in range(m.w) if m.grid[y][x] == PATH}
    for cell in list(m.anchors.values()):
        m.carve_to_net(cell, net)


def _seal(m: _Map) -> None:
    """Wall every open cell unreachable from the first anchor — after this, open == walkable,
    so a snapped hotspot can never land in a sealed pocket."""
    if not m.anchors:
        return
    free = m.open_set()
    keep = _flood(free, next(iter(m.anchors.values()))) | set(m.anchors.values())
    for x, y in free - keep:
        m.grid[y][x] = WALL


# ── the three generators ─────────────────────────────────────────────────────
def _gen_town(m: _Map, layout: Dict):
    m.ring()
    m.bite_corners()
    throats = [m.place_exit(ex["id"], ex["edge"]) for ex in _exits_of(layout)]
    plaza = (m.w // 2, m.h // 2)
    net: set = {plaza}
    m.grid[plaza[1]][plaza[0]] = PATH
    m.reserved.add(plaza)
    if throats:
        for t in throats:
            m.carve_to_net(t, net)
    else:
        m.carve_to_net((2, m.h // 2), net)
        m.carve_to_net((m.w - 3, m.h // 2), net)

    def solid_cands(dims, prefer):
        return _road_candidates(m, dims, prefer) + _scatter_candidates(m, dims, prefer)

    for feat in _features_of(layout):
        _place_feature(m, feat, solid_cands)
    return solid_cands


def _gen_interior(m: _Map, layout: Dict):
    m.ring()
    splits: List[Tuple] = []

    def split(x0, y0, x1, y1, d):
        w_, h_ = x1 - x0 + 1, y1 - y0 + 1
        if d > 0 and (w_ >= 9 or h_ >= 9):
            if w_ >= h_:
                xs = m.rng.randrange(x0 + 4, x1 - 3)
                for y in range(y0, y1 + 1):
                    m.grid[y][xs] = WALL
                splits.append((("v", xs), (y0, y1)))
                split(x0, y0, xs - 1, y1, d - 1)
                split(xs + 1, y0, x1, y1, d - 1)
            else:
                ys = m.rng.randrange(y0 + 4, y1 - 3)
                for x in range(x0, x1 + 1):
                    m.grid[ys][x] = WALL
                splits.append((("h", ys), (x0, x1)))
                split(x0, y0, x1, ys - 1, d - 1)
                split(x0, ys + 1, x1, y1, d - 1)

    depth = 2 if m.w * m.h >= SIZES["medium"][0] * SIZES["medium"][1] else 1
    split(1, 1, m.w - 2, m.h - 2, depth)

    for (ori, line), (a0, a1) in splits:
        mid = (a0 + a1) // 2
        for pos in sorted(range(a0, a1 + 1), key=lambda p: (abs(p - mid), p)):
            x, y = (line, pos) if ori == "v" else (pos, line)
            sides = ((x - 1, y), (x + 1, y)) if ori == "v" else ((x, y - 1), (x, y + 1))
            if all(m.grid[sy][sx] == OPEN for sx, sy in sides):
                m.grid[y][x] = PATH
                m.musts.append((x, y))
                m.reserved.add((x, y))
                for s in sides:   # keep the throat clear so furniture can't blockade the door
                    m.reserved.add(s)
                    m.musts.append(s)
                break

    for ex in _exits_of(layout):
        m.place_exit(ex["id"], ex["edge"])

    def solid_cands(dims, prefer):
        return _scatter_candidates(m, dims, prefer, near_wall=True)

    for feat in _features_of(layout):
        _place_feature(m, feat, solid_cands)
    return solid_cands


def _gen_wild(m: _Map, layout: Dict):
    m.ring()
    for y in range(1, m.h - 1):
        for x in range(1, m.w - 1):
            d = min(x, y, m.w - 1 - x, m.h - 1 - y)
            if (d == 1 and m.rng.random() < 0.30) or (d == 2 and m.rng.random() < 0.10):
                m.grid[y][x] = WALL

    for ex in _exits_of(layout):
        m.place_exit(ex["id"], ex["edge"])

    def solid_cands(dims, prefer):
        return _scatter_candidates(m, dims, prefer)

    for feat in _features_of(layout):
        _place_feature(m, feat, solid_cands)

    # the path spine, then organic masses grown around it (each blob atomic + guarded)
    _link(m)
    target = (m.w * m.h) // 7
    walled = 0
    for _ in range(target):
        if walled >= target:
            break
        cx, cy = m.rng.randrange(2, m.w - 2), m.rng.randrange(2, m.h - 2)
        size = m.rng.randint(3, 7)
        blob: List[Tuple[int, int]] = []
        for _ in range(size * 3):
            if len(blob) >= size:
                break
            if m.placeable(cx, cy) and (cx, cy) not in blob:
                blob.append((cx, cy))
            dx, dy = m.rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
            cx, cy = cx + dx, cy + dy
        if len(blob) < 2:
            continue
        for bx, by in blob:
            m.grid[by][bx] = WALL
        if m.connected():
            walled += len(blob)
        else:
            for bx, by in blob:
                m.grid[by][bx] = OPEN
    return solid_cands


_GENERATORS = {"town": _gen_town, "interior": _gen_interior}


def build_tiles(zone_id: str, layout: Dict) -> Dict:
    """Rasterize a layout → {"rows", "legend", "anchors", "footprints"}. Deterministic per
    zone_id; furniture is placed last, so re-building with a (new) furniture list never moves
    a feature or its anchor."""
    m = _Map(zone_id, layout)
    gen = _GENERATORS.get(layout.get("kind"), _gen_wild)
    furn_cands = gen(m, layout)
    _link(m)
    _furnish(m, layout, furn_cands)
    _seal(m)

    terrain = layout.get("terrain") or {}
    open_theme = terrain.get("open") or "grass"
    blocked_theme = terrain.get("blocked") or (
        "stone wall" if layout.get("kind") == "interior" else "dense brush")
    rows = ["".join(r) for r in m.grid]
    present = {ch for row in rows for ch in row}
    legend = {
        ".": {"role": "open", "theme": open_theme},
        ",": {"role": "open", "theme": f"worn {open_theme} path"},
        "#": {"role": "blocked", "theme": blocked_theme},
        **{ch: {"role": "blocked", "theme": theme}
           for ch, theme in m._char_theme.items() if ch in present},
    }
    return {"rows": rows, "legend": legend,
            "anchors": {k: {"x": v[0], "y": v[1]} for k, v in m.anchors.items()},
            "footprints": m.footprints}


def render_ascii(built: Dict) -> str:
    """The built map as printable text — rows with anchors overlaid (lowercase marks + a key)
    and the legend, so a human can SEE a fixture map in a test failure."""
    rows = [list(r) for r in built.get("rows") or []]
    marks = "abcdefghijklmnopqrstuvwxyz"
    key = []
    for i, (aid, a) in enumerate(sorted((built.get("anchors") or {}).items())):
        ch = marks[i % len(marks)]
        if 0 <= a["y"] < len(rows) and 0 <= a["x"] < len(rows[a["y"]]):
            rows[a["y"]][a["x"]] = ch
        key.append(f"{ch}={aid}({a['x']},{a['y']})")
    lines = ["".join(r) for r in rows]
    if key:
        lines.append("anchors: " + "  ".join(key))
    lines += [f"legend {ch!r}: {e.get('role')}/{e.get('theme')}"
              for ch, e in sorted((built.get("legend") or {}).items())]
    return "\n".join(lines)


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
        return "layout must be an object {size, terrain, features, exits}"
    if layout.get("size") not in SIZES:
        return f"layout.size must be one of {sorted(SIZES)}"
    feats = layout.get("features")
    if not isinstance(feats, list) or not feats:
        return ("layout.features must be a non-empty list of {id, size, at, label, theme?} — "
                "one feature per spot an interactable stands at (ambient scenery comes from "
                "the furniture list, not features)")
    ids = set()
    for i, f in enumerate(feats):
        if not isinstance(f, dict) or not f.get("id"):
            return f"layout.features[{i}] needs an 'id'"
        if f.get("size") not in FEATURE_SIZES:
            return (f"layout.features[{i}].size {f.get('size')!r} must be one of "
                    f"{sorted(FEATURE_SIZES)} — spot: one walkable tile (a gate, a signpost); "
                    f"small/medium/large: a solid object scaled to the avatar; "
                    f"area: an open patch (a plaza, a glade)")
        if f.get("at") not in REGIONS:
            return (f"layout.features[{i}].at {f.get('at')!r} must be one of {sorted(REGIONS)} "
                    f"(a coarse 3x3 region — the builder places the exact cells)")
        if not f.get("label") or not isinstance(f.get("label"), str):
            return (f"layout.features[{i}] needs a 'label' — its display name "
                    f"(it also names the object's sprite)")
        ids.add(f["id"])
    for i, ex in enumerate(layout.get("exits") or []):
        if not isinstance(ex, dict) or ex.get("edge") not in _EDGES or not ex.get("id"):
            return f"layout.exits[{i}] needs an 'id' and an 'edge' in {_EDGES}"
        ids.add(ex["id"])
    if layout.get("furniture") is not None:
        return v_furniture(layout["furniture"])
    return None


def v_furniture(furniture) -> Optional[str]:
    if not isinstance(furniture, list) or not furniture:
        return "furniture must be a non-empty list of {object, size, flavor?} entries"
    if len(furniture) > 12:
        return "furniture lists at most 12 objects — keep the ones that most make the place itself"
    for i, f in enumerate(furniture):
        if not isinstance(f, dict) or not f.get("object") or not isinstance(f.get("object"), str):
            return f"furniture[{i}] needs an 'object' — the concrete thing it is"
        if f.get("size") not in FURNITURE_SIZES:
            return (f"furniture[{i}].size {f.get('size')!r} must be one of "
                    f"{sorted(FURNITURE_SIZES)} — relative to the avatar "
                    f"(small: a barrel; medium: a cart; large: a shed)")
        if f.get("flavor") is not None and not isinstance(f["flavor"], str):
            return f"furniture[{i}].flavor must be a string (one physical fact) when present"
    return None
