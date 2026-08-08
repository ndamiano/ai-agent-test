"""The solver behind compose_scene's blockout: the LLM plans RELATIONS — what exists, how
many, what sits near what — and never a coordinate; this code places everything
deterministically. Terrain paints real bands, roads are a Dijkstra network with an
existing-road discount so routes snap together by construction, buildings attach to
road-adjacent pivots, and the walkable grid is emitted as ground truth (roads minus building
wall footprints, reduced to one connected component with every POI snapped onto it, so the
map contract never strands a placeable the greedy solver did).
"""

import heapq
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from scipy import ndimage as ndi

ROOF_MARGIN = 3  # cells of headroom a roof + eaves needs beyond the wall block

BASE_TERRAINS = {"grass", "meadow", "sand", "snow", "dirt", "rock"}
Point = Tuple[int, int]


@dataclass
class Placeable:
    name: str
    kind: str
    size: str
    count: int = 1
    poi: bool = True


@dataclass
class Constraint:
    type: str
    args: List[str]


def _size_to_cells(size: str) -> Tuple[int, int]:
    if size == "small":
        return (2, 2)
    elif size == "medium":
        return (3, 3)
    else:
        return (4, 4)


def _distance_to_edge(x: int, y: int, edge: str, w: int, h: int) -> int:
    if edge == "north":
        return y
    elif edge == "south":
        return h - y
    elif edge == "west":
        return x
    elif edge == "east":
        return w - x
    return 100


def _score_placement(x: int, y: int, name: str, kind: str,
                     positions: Dict[str, Tuple[int, int]],
                     constraints: List[Constraint], w: int, h: int) -> float:
    score = 0.0
    cx, cy = x + 1, y + 1

    for c in constraints:
        if c.type == "near" and len(c.args) >= 2:
            a, b = c.args[0], c.args[1]
            if a == name and b in positions:
                bx, by = positions[b]
                dist = max(abs(cx - bx), abs(cy - by))
                score += max(0, 10 - dist)
        elif c.type == "far" and len(c.args) >= 2:
            a, b = c.args[0], c.args[1]
            if a == name and b in positions:
                bx, by = positions[b]
                dist = max(abs(cx - bx), abs(cy - by))
                score += min(dist, 20)
        elif c.type == "on_edge" and len(c.args) >= 2:
            a, edge = c.args[0], c.args[1]
            if a == name:
                score += max(0, 10 - _distance_to_edge(cx, cy, edge, w, h))
        elif c.type == "central" and len(c.args) >= 1:
            if c.args[0] == name:
                dist_to_center = max(abs(cx - w // 2), abs(cy - h // 2))
                score += max(0, 10 - dist_to_center)
        elif c.type == "beside_road" and len(c.args) >= 1:
            if c.args[0] == name and kind == "building":
                score += 5

    return score


def _edge_slice(direction: str, depth: int, w: int, h: int,
                inboard: int = 0) -> Tuple[slice, slice]:
    if direction == "south":
        return slice(h - depth - inboard, h - inboard), slice(0, w)
    if direction == "north":
        return slice(inboard, depth + inboard), slice(0, w)
    if direction == "east":
        return slice(0, h), slice(w - depth - inboard, w - inboard)
    if direction == "west":
        return slice(0, h), slice(inboard, depth + inboard)
    return slice(0, 0), slice(0, 0)


def _build_terrain(rnd: random.Random, base: str, features: List[str], w: int, h: int):
    if base not in BASE_TERRAINS:
        base = "grass"
    names = [base]
    water = np.zeros((h, w), bool)
    sand = np.zeros((h, w), bool)
    forest = np.zeros((h, w), bool)
    rock = np.zeros((h, w), bool)
    lava = np.zeros((h, w), bool)
    exclude_sides: Set[str] = set()
    force_axis: Optional[str] = None

    for feat in features:
        if not isinstance(feat, str):
            continue
        if "water_edge" in feat:
            direction = feat.split(":")[1] if ":" in feat else "south"
            ys, xs = _edge_slice(direction, 6, w, h)
            water[ys, xs] = True
            ys2, xs2 = _edge_slice(direction, 2, w, h, 6)
            sand[ys2, xs2] = True
            exclude_sides.add(direction[0].upper())
        elif "lava_edge" in feat:
            direction = feat.split(":")[1] if ":" in feat else "north"
            ys, xs = _edge_slice(direction, 5, w, h)
            lava[ys, xs] = True
            exclude_sides.add(direction[0].upper())
        elif "cliff_edge" in feat:
            direction = feat.split(":")[1] if ":" in feat else "north"
            ys, xs = _edge_slice(direction, 5, w, h)
            rock[ys, xs] = True
            exclude_sides.add(direction[0].upper())
        elif "forest_edge" in feat:
            direction = feat.split(":")[1] if ":" in feat else "north"
            ys, xs = _edge_slice(direction, 8, w, h)
            forest[ys, xs] = True
        elif "river" in feat:
            target = lava if feat.startswith("lava_river") else water
            axis = feat.split(":")[1] if ":" in feat else "north-south"
            rw = rnd.choice([2, 3])
            if axis == "north-south":
                cx = w // 2 + rnd.randint(-4, 4)
                target[:, cx:cx + rw] = True
                force_axis = "EW"
            else:
                cy = h // 2 + rnd.randint(-3, 3)
                target[cy:cy + rw, :] = True
                force_axis = "NS"

    labels = np.zeros((h, w), int)

    def put(mask: np.ndarray, name: str) -> None:
        if not mask.any():
            return
        if name not in names:
            names.append(name)
        labels[mask] = names.index(name)

    put(sand, "sand")
    put(forest, "forest")
    put(rock, "rock")
    put(water, "water")
    put(lava, "lava")

    hazard = water | lava | rock
    base_cost = {"sand": 2.0, "snow": 1.5, "rock": 2.5}.get(base, 1.0)
    cost = np.full((h, w), base_cost, float)
    cost[forest] = max(base_cost, 1.8)
    cost[hazard] = 25.0

    return labels, names, hazard, cost, exclude_sides, force_axis


def _dijkstra(cost_of, start: Point, is_goal, w: int, h: int) -> Optional[List[Point]]:
    dist: Dict[Point, float] = {start: 0.0}
    prev: Dict[Point, Point] = {}
    heap: List[Tuple[float, Point]] = [(0.0, start)]
    seen = set()
    goal = None
    while heap:
        d, u = heapq.heappop(heap)
        if u in seen:
            continue
        seen.add(u)
        if is_goal(u):
            goal = u
            break
        x, y = u
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                v = (nx, ny)
                nd = d + cost_of(v)
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
    if goal is None:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def _hub_points(rnd: random.Random, hazard: np.ndarray, n: int,
                w: int, h: int) -> List[Point]:
    quads = [(0, 0), (1, 0), (0, 1), (1, 1)]
    rnd.shuffle(quads)
    pts: List[Point] = []
    for i in range(n):
        qx, qy = quads[i % 4]
        x0, x1 = int(qx * w / 2) + 4, int((qx + 1) * w / 2) - 4
        y0, y1 = int(qy * h / 2) + 4, int((qy + 1) * h / 2) - 4
        for _ in range(30):
            x = rnd.randint(x0, max(x0, x1 - 1))
            y = rnd.randint(y0, max(y0, y1 - 1))
            if not hazard[y, x]:
                pts.append((x, y))
                break
    return pts


def _place_roads(rnd: random.Random, cost: np.ndarray, hazard: np.ndarray,
                 exclude_sides: Set[str], force_axis: Optional[str],
                 n_buildings: int, w: int, h: int) -> Tuple[np.ndarray, List[Point]]:
    road_mask = np.zeros((h, w), bool)

    def cost_of(v: Point) -> float:
        x, y = v
        return 0.3 if road_mask[y, x] else float(cost[y, x])

    def gate(side: str) -> Point:
        if side == "N":
            return (rnd.randint(3, w - 4), 0)
        if side == "S":
            return (rnd.randint(3, w - 4), h - 1)
        if side == "E":
            return (w - 1, rnd.randint(3, h - 4))
        return (0, rnd.randint(3, h - 4))

    sides = [s for s in ("N", "S", "E", "W") if s not in exclude_sides] or list("NSEW")
    rnd.shuffle(sides)
    num_gates = 3 if n_buildings >= 4 else 2

    if force_axis == "EW":
        gates = [(0, rnd.randint(3, h - 4)), (w - 1, rnd.randint(3, h - 4))]
        extra = [s for s in sides if s not in ("E", "W")]
        if num_gates > 2 and extra:
            gates.append(gate(extra[0]))
    elif force_axis == "NS":
        gates = [(rnd.randint(3, w - 4), 0), (rnd.randint(3, w - 4), h - 1)]
        extra = [s for s in sides if s not in ("N", "S")]
        if num_gates > 2 and extra:
            gates.append(gate(extra[0]))
    else:
        gates = [gate(s) for s in sides[:num_gates]]

    n_hubs = 4 if n_buildings >= 6 else 3
    hubs = _hub_points(rnd, hazard, n_hubs, w, h)
    center = (w // 2, h // 2)

    endpoints = gates + hubs
    for i, ep in enumerate(endpoints):
        if i == 0:
            path = _dijkstra(cost_of, ep, lambda u: u == center, w, h)
        else:
            path = _dijkstra(cost_of, ep, lambda u: road_mask[u[1], u[0]], w, h)
        if path:
            for x, y in path:
                road_mask[y, x] = True

    return road_mask, gates


def _region(direction: str, rx: int, ry: int, wc: int, hw: int, margin: int):
    if direction == "N":
        return ry - hw - margin, ry, rx - wc // 2, rx - wc // 2 + wc
    if direction == "S":
        return ry + 1, ry + 1 + hw + margin, rx - wc // 2, rx - wc // 2 + wc
    if direction == "E":
        return ry - wc // 2, ry - wc // 2 + wc, rx + 1, rx + 1 + hw + margin
    return ry - wc // 2, ry - wc // 2 + wc, rx - 1 - hw - margin, rx - 1  # W


def _wall(direction: str, rx: int, ry: int, wc: int, hw: int):
    return _region(direction, rx, ry, wc, hw, 0)


def _place_buildings(rnd: random.Random, buildings: List[Placeable],
                     positions: Dict[str, Tuple[int, int]],
                     constraints: List[Constraint], road_mask: np.ndarray,
                     hazard: np.ndarray, occupied: np.ndarray,
                     w: int, h: int) -> List[Dict]:
    road_cells = [(int(x), int(y)) for y, x in zip(*np.nonzero(road_mask))]
    placed: List[Dict] = []
    for b in buildings:
        wc, hw = _size_to_cells(b.size)
        rnd.shuffle(road_cells)
        best = None
        best_score = -1e9
        for rx, ry in road_cells:
            dirs = ["N", "S", "E", "W"]
            rnd.shuffle(dirs)
            for direction in dirs:
                r0, r1, c0, c1 = _region(direction, rx, ry, wc, hw, ROOF_MARGIN)
                if r0 < 1 or c0 < 1 or r1 > h - 1 or c1 > w - 1:
                    continue
                # pad the candidate's own box by 1 cell before checking `occupied` — the
                # roof's eave pixels reach a few px past the margin box, so two buildings
                # whose (unpadded) margin boxes only TOUCH still show overlapping eaves
                pr0, pr1 = max(0, r0 - 1), min(h, r1 + 1)
                pc0, pc1 = max(0, c0 - 1), min(w, c1 + 1)
                if occupied[pr0:pr1, pc0:pc1].any() or hazard[r0:r1, c0:c1].any():
                    continue
                # the footprint (beyond the pivot cell itself, which this region excludes by
                # construction) must not eat a road cell elsewhere in the network — that
                # silently strands whatever was reached through it, including other doors
                if road_mask[r0:r1, c0:c1].any():
                    continue
                wr0, wr1, wc0, wc1 = _wall(direction, rx, ry, wc, hw)
                cx, cy = (wc0 + wc1) // 2, (wr0 + wr1) // 2
                score = _score_placement(cx, cy, b.name, "building", positions,
                                         constraints, w, h)
                if score > best_score:
                    best_score = score
                    best = (rx, ry, direction, r0, r1, c0, c1, wr0, wr1, wc0, wc1)
        if best is None:
            continue
        rx, ry, direction, r0, r1, c0, c1, wr0, wr1, wc0, wc1 = best
        occupied[r0:r1, c0:c1] = True
        positions[b.name] = (wc0, wr0)
        placed.append({"name": b.name, "rx": rx, "ry": ry, "dir": direction,
                       "wc": wc, "hw": hw, "wall": (wr0, wr1, wc0, wc1),
                       "poi": b.poi})
    return placed


def _zone_label(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("market", "bazaar", "square", "plaza")):
        return "plaza"
    if any(k in n for k in ("field", "farm", "pasture", "crop")):
        return "field"
    if any(k in n for k in ("garden", "grove", "park")):
        return "meadow"
    if any(k in n for k in ("camp", "yard", "court")):
        return "dirt"
    return "plaza"


def _place_zone(z: Placeable, positions: Dict[str, Tuple[int, int]],
                constraints: List[Constraint], hazard: np.ndarray,
                occupied: np.ndarray, w: int, h: int
                ) -> Optional[Tuple[int, int, int, int]]:
    zw, zh = _size_to_cells(z.size)
    best = None
    best_score = -1e9
    for y in range(1, h - zh - 1, 2):
        for x in range(1, w - zw - 1, 2):
            if occupied[y:y + zh, x:x + zw].any() or hazard[y:y + zh, x:x + zw].any():
                continue
            cx, cy = x + zw // 2, y + zh // 2
            score = _score_placement(cx, cy, z.name, "zone", positions, constraints, w, h)
            if score > best_score:
                best_score = score
                best = (x, y)
    if best is None:
        return None
    x, y = best
    occupied[y:y + zh, x:x + zw] = True
    positions[z.name] = (x, y)
    return (x, y, zw, zh)


def _place_landmark(l: Placeable, positions: Dict[str, Tuple[int, int]],
                    constraints: List[Constraint], hazard: np.ndarray,
                    occupied: np.ndarray, w: int, h: int
                    ) -> Optional[Tuple[int, int, int, int, int, int]]:
    best = None
    best_score = -1e9
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if occupied[y, x] or hazard[y, x]:
                continue
            score = _score_placement(x, y, l.name, "landmark", positions, constraints, w, h)
            if score > best_score:
                best_score = score
                best = (x, y)
    if best is None:
        return None
    x, y = best
    positions[l.name] = best
    x0, x1 = max(0, x - 1), min(w, x + 1)
    y0, y1 = max(0, y - 1), min(h, y + 1)
    occupied[y0:y1, x0:x1] = True
    return (x, y, x0, y0, x1, y1)


_BOATISH = ("boat", "ship", "raft", "canoe")


def _place_decorations(rnd: random.Random, decos: List[Placeable],
                       positions: Dict[str, Tuple[int, int]],
                       constraints: List[Constraint], hazard: np.ndarray,
                       occupied: np.ndarray, road_mask: np.ndarray,
                       water_mask: np.ndarray, w: int, h: int
                       ) -> List[Tuple[str, int, int, int]]:
    placed: List[Tuple[str, int, int, int]] = []
    for d in decos:
        cells = 1 if d.size == "small" else 2
        wants_water = any(k in d.name.lower() for k in _BOATISH)
        best, best_score = None, -1e9
        for _ in range(400):
            x = rnd.randint(1, w - cells - 1)
            y = rnd.randint(1, h - cells - 1)
            box = (slice(y, y + cells), slice(x, x + cells))
            if occupied[box].any() or road_mask[box].any():
                continue
            if wants_water:
                if not water_mask[box].all():
                    continue
            elif hazard[box].any():
                continue
            score = _score_placement(x, y, d.name, "decoration", positions,
                                     constraints, w, h) + rnd.random()
            if score > best_score:
                best_score, best = score, (x, y)
        if best is None:
            continue
        x, y = best
        occupied[y:y + cells, x:x + cells] = True
        positions.setdefault(d.name, (x, y))
        placed.append((d.name, x, y, cells))
    return placed


def solve(plan: dict, request: str, seed: int, w: int = 48, h: int = 36) -> dict:
    if not isinstance(plan, dict) or "terrain" not in plan or "placeables" not in plan:
        plan = {"terrain": {"base": "grass", "features": []},
                "placeables": [{"name": "plaza", "kind": "zone", "size": "large"}],
                "constraints": [["central", "plaza"]]}

    terrain = plan.get("terrain", {}) or {}
    base = terrain.get("base", "grass") if isinstance(terrain, dict) else "grass"
    features = (terrain.get("features", []) or []) if isinstance(terrain, dict) else []

    def _count(p) -> int:
        try:
            return max(1, min(24, int(p.get("count", 1))))
        except (TypeError, ValueError):
            return 1

    placeables = [Placeable(name=str(p.get("name", "?")), kind=p.get("kind", "zone"),
                            size=p.get("size", "medium"), count=_count(p))
                  for p in plan.get("placeables", []) or [] if isinstance(p, dict)]

    constraints = [Constraint(type=c[0], args=[str(a) for a in c[1:]])
                   for c in plan.get("constraints", []) or []
                   if isinstance(c, list) and c]

    rnd = random.Random(f"{request}|{seed}")
    labels, names, hazard, cost, exclude_sides, force_axis = _build_terrain(
        rnd, base, features, w, h)

    def _expand(kind: str) -> List[Placeable]:
        out = []
        for p in placeables:
            if p.kind != kind:
                continue
            for _ in range(p.count):
                out.append(Placeable(name=p.name, kind=p.kind, size=p.size,
                                     poi=(p.count == 1)))
        return out

    buildings_plan = _expand("building")
    zones_plan = _expand("zone")
    landmarks_plan = _expand("landmark")
    decos_plan = _expand("decoration")

    road_mask, gates = _place_roads(rnd, cost, hazard, exclude_sides, force_axis,
                                    len(buildings_plan), w, h)

    occupied = np.zeros((h, w), bool)
    positions: Dict[str, Tuple[int, int]] = {}

    buildings = _place_buildings(rnd, buildings_plan, positions, constraints,
                                 road_mask, hazard, occupied, w, h)

    zone_patches: List[Tuple[str, Tuple[int, int, int, int]]] = []
    for z in zones_plan:
        patch = _place_zone(z, positions, constraints, hazard, occupied, w, h)
        if patch:
            zone_patches.append((z.name, patch))

    landmark_patches: List[Tuple[str, Tuple[int, int, int, int, int, int]]] = []
    for l in landmarks_plan:
        patch = _place_landmark(l, positions, constraints, hazard, occupied, w, h)
        if patch:
            landmark_patches.append((l.name, patch))

    water_mask = np.zeros((h, w), bool)
    for wet in ("water", "deep", "lava"):
        if wet in names:
            water_mask |= labels == names.index(wet)
    decorations = _place_decorations(rnd, decos_plan, positions, constraints,
                                     hazard, occupied, road_mask, water_mask, w, h)

    # zones then landmarks then roads last — a road always cuts visibly through whatever it
    # crosses, including water: that crossing IS the bridge
    for name, (x, y, zw, zh) in zone_patches:
        label = _zone_label(name)
        if label not in names:
            names.append(label)
        labels[y:y + zh, x:x + zw] = names.index(label)

    if landmark_patches:
        if "rock" not in names:
            names.append("rock")
        rock_idx = names.index("rock")
        for _, (_, _, x0, y0, x1, y1) in landmark_patches:
            labels[y0:y1, x0:x1] = rock_idx

    if "road" not in names:
        names.append("road")
    labels[road_mask] = names.index("road")

    pois: List[Dict] = []
    doors: List[List[int]] = []
    for b in buildings:
        doors.append([b["rx"], b["ry"]])
        if b["poi"]:
            pois.append({"name": b["name"], "x": b["rx"], "y": b["ry"]})
    for name, (x, y, zw, zh) in zone_patches:
        pois.append({"name": name, "x": x + zw // 2, "y": y + zh // 2})
    for name, (x, y, x0, y0, x1, y1) in landmark_patches:
        pois.append({"name": name, "x": x, "y": y})

    # walkable = roads minus the building wall footprints (a roof may overhang the street
    # without blocking it — only the wall region, not the roof margin, clears)
    walk = road_mask.copy()
    for b in buildings:
        wr0, wr1, wc0, wc1 = b["wall"]
        walk[wr0:wr1, wc0:wc1] = False

    # one connected component, and every POI snapped onto it — the solver may strand a
    # placeable off the network; the map contract may not
    comp, ncomp = ndi.label(walk)
    if ncomp > 1:
        keep = np.argmax(np.bincount(comp.ravel())[1:]) + 1
        walk = comp == keep
    wy, wx = np.nonzero(walk)
    if len(wx):
        for p in pois:
            if not walk[p["y"], p["x"]]:
                i = int(np.argmin((wx - p["x"]) ** 2 + (wy - p["y"]) ** 2))
                p["x"], p["y"] = int(wx[i]), int(wy[i])

    boxes: List[Dict] = []
    for b in buildings:
        wr0, wr1, wc0, wc1 = b["wall"]
        boxes.append({"kind": "building", "name": b["name"], "x": wc0, "y": wr0,
                      "w": wc1 - wc0, "h": wr1 - wr0})
    for name, (x, y, zw, zh) in zone_patches:
        boxes.append({"kind": "zone", "name": name, "x": x, "y": y, "w": zw, "h": zh})
    for name, (x, y, x0, y0, x1, y1) in landmark_patches:
        boxes.append({"kind": "landmark", "name": name, "x": x0, "y": y0,
                      "w": x1 - x0, "h": y1 - y0})
    for name, dx, dy, cells in decorations:
        boxes.append({"kind": "decoration", "name": name, "x": dx, "y": dy,
                      "w": cells, "h": cells})

    return {
        "blockout": {
            "cells": [w, h],
            "terrain_grid": ["".join(str(min(9, v)) for v in row)
                             for row in labels.tolist()],
            "terrain_names": names,
            "boxes": boxes,
        },
        "scene": {
            "seed": seed,
            "width_cells": w,
            "height_cells": h,
            "walkable": ["".join("1" if c else "0" for c in row) for row in walk],
            "doors": doors,
            "pois": pois,
        },
    }
