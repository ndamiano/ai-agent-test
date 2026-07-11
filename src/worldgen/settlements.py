from .context import connected_cells
from .errors import PlacementError
from .wants import is_coastal, score_candidate

_FOOTPRINT_CELLS = 6


def _min_distance(w, h):
    return max(4.0, min(w, h) * 0.15)


def place_settlements(locations, ctx):
    settlements = [loc for loc in locations if loc.get("type") == "settlement"]
    placed = []
    min_dist = _min_distance(ctx.w, ctx.h)

    for loc in settlements:
        want = loc.get("want", "")
        wants_coastal = "coastal" in want.lower() or "harbor" in want.lower()

        best = None
        best_score = None
        for y in range(ctx.h):
            for x in range(ctx.w):
                if ctx.water[y][x]:
                    continue
                if wants_coastal and not is_coastal(x, y, ctx.water, ctx.w, ctx.h):
                    continue
                if any(((x - p["x"]) ** 2 + (y - p["y"]) ** 2) ** 0.5 < min_dist for p in placed):
                    continue
                s = score_candidate(x, y, want, ctx, placed)
                if s is None:
                    continue
                if best_score is None or s > best_score or (s == best_score and (y, x) < (best[1], best[0])):
                    best_score = s
                    best = (x, y)

        if best is None:
            raise PlacementError(f"no site for settlement {loc['id']!r}")

        x, y = best
        cells = connected_cells(ctx.w, ctx.h, ctx.water, (x, y), False, _FOOTPRINT_CELLS)
        placed.append({"id": loc["id"], "type": "settlement", "x": x, "y": y, "cells": [list(c) for c in cells]})

    return placed
