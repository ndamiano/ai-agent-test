from .context import connected_cells
from .errors import PlacementError
from .wants import is_coastal, score_candidate

_FOOTPRINT_CELLS = {
    "coastal_strip": 8,
    "wilderness": 20,
    "open_water": 20,
    "landmark": 3,
}
_OPEN_WATER_LARGE_CELLS = 60


def _domain(loc_type, want, ctx, x, y):
    if loc_type == "open_water":
        return ctx.water[y][x]
    if ctx.water[y][x]:
        return False
    if loc_type == "coastal_strip":
        return is_coastal(x, y, ctx.water, ctx.w, ctx.h)
    return True


def _place_one(loc, ctx, placed):
    loc_type = loc["type"]
    want = loc.get("want", "")

    best = None
    best_score = None
    for y in range(ctx.h):
        for x in range(ctx.w):
            if not _domain(loc_type, want, ctx, x, y):
                continue
            s = score_candidate(x, y, want, ctx, placed)
            if s is None:
                continue
            if best_score is None or s > best_score or (s == best_score and (y, x) < (best[1], best[0])):
                best_score = s
                best = (x, y)

    if best is None:
        raise PlacementError(f"no site for {loc['id']!r} ({loc_type})")

    x, y = best
    cap = _FOOTPRINT_CELLS[loc_type]
    if loc_type == "open_water" and "large" in want.lower():
        cap = _OPEN_WATER_LARGE_CELLS
    kind = loc_type == "open_water"
    cells = connected_cells(ctx.w, ctx.h, ctx.water, (x, y), kind, cap)
    return {"id": loc["id"], "type": loc_type, "x": x, "y": y, "cells": [list(c) for c in cells]}


def place_remaining(locations, settlements, ctx):
    placed = list(settlements)
    others = [loc for loc in locations if loc.get("type") not in ("settlement", "interior")]
    for loc in others:
        placed.append(_place_one(loc, ctx, placed))

    interiors = [loc for loc in locations if loc.get("type") == "interior"]
    by_id = {s["id"]: s for s in placed}
    for loc in interiors:
        host = by_id.get(loc["host"])
        if host is None:
            raise PlacementError(f"interior {loc['id']!r} has no placed host {loc['host']!r}")
        placed.append({
            "id": loc["id"], "type": "interior", "host": loc["host"],
            "x": host["x"], "y": host["y"], "cells": [],
        })

    return placed
