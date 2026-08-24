"""LLM-layered 2D tile-map layout: small single-purpose calls, skeleton-first, validated.

Measured 2026-08-23 (scene-gen lab, 14 places): this shape completes 12/12 maps on a 27B
with zero pipeline failures and fully connected walkable ground, where the relations →
constraint-solve blockout it replaces produced one road-spine "tunnel" per archetype.
Self-critique rounds measured flat and their rect fix-ops caused the worst artifacts, so
there are none. Asked to rewrite a full-resolution grid directly the model emits uniform
fill — every stage edits through its own small representation instead.

The whole module is deliberately maestro-free: callers inject `llm(messages, max_tokens)
-> str` and get plain dicts back, so the folder can be copied into a lab and iterated
against any OpenAI-style endpoint.
"""
from __future__ import annotations

import copy
import json
import re
from collections import deque
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SCALE = 4
_PROMPTS = Path(__file__).parent / "prompts"

Llm = Callable[..., str]


def _prompt(name: str, **subst: str) -> str:
    text = (_PROMPTS / f"{name}.txt").read_text(encoding="utf-8")
    for key, value in subst.items():
        text = text.replace("{" + key + "}", str(value))
    return text



def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_json(text: str):
    text = _strip_think(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    for start in (i for i, ch in enumerate(text) if ch in "{["):
        opener = text[start]
        closer = "}" if opener == "{" else "]"
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"No parseable JSON in response:\n{text[:2000]}")


def chat_json(llm: Llm, prompt: str, validator=None, max_tokens: int = 2048, retries: int = 2):
    messages = [{"role": "user", "content": prompt}]
    last_err: Optional[Exception] = None
    for _ in range(retries + 1):
        content = llm(messages, max_tokens=max_tokens)
        try:
            obj = extract_json(content)
            if validator:
                err = validator(obj)
                if err:
                    raise ValueError(err)
            return obj
        except ValueError as e:
            last_err = e
            messages = [{"role": "user", "content": prompt},
                        {"role": "assistant", "content": _strip_think(content)[:3000]},
                        {"role": "user", "content":
                         f"That response had a problem: {e}\n"
                         "Reply again with only the corrected JSON, nothing else."}]
    raise RuntimeError(f"layout call failed after {retries + 1} attempts: {last_err}")



def stage_tileset(llm: Llm, place: str) -> List[Dict]:
    def validate(obj):
        t = obj.get("terrain")
        if not isinstance(t, list) or not (3 <= len(t) <= 8):
            return "terrain must be a list of 3 to 8 tiles"
        syms = [d.get("symbol", "") for d in t]
        if any(not (isinstance(s, str) and len(s) == 1 and s.isalpha() and s.isupper())
               for s in syms):
            return "every symbol must be a single uppercase letter"
        if len(set(syms)) != len(syms):
            return "symbols must be unique"
        if any(not isinstance(d.get("walkable"), bool) for d in t):
            return "every tile needs walkable true or false"
        if not any(d["walkable"] for d in t):
            return "at least one terrain must be walkable"
        return None

    terrain = chat_json(llm, _prompt("tileset", place=place), validate)["terrain"]
    for t in terrain:
        c = t.get("color")
        if not (isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c.strip())):
            t["color"] = "#9e9e9e"
        else:
            t["color"] = c.strip()
    return terrain


def stage_items(llm: Llm, place: str, terrain: List[Dict]) -> List[Dict]:
    terrain_syms = {t["symbol"] for t in terrain}
    desc = json.dumps([{k: t[k] for k in ("symbol", "name", "walkable")} for t in terrain])

    def validate(obj):
        objs, ents = obj.get("objects"), obj.get("entities")
        if not isinstance(objs, list) or not isinstance(ents, list):
            return "reply needs both an objects list and an entities list"
        seen = set(terrain_syms)
        for d in objs + ents:
            s = d.get("symbol", "")
            if not (isinstance(s, str) and len(s) == 1 and s.islower()):
                return "every object and entity symbol must be a single lowercase letter"
            if s in seen:
                return f"symbol '{s}' is already used; each symbol must be unique"
            seen.add(s)
            if d.get("on_terrain") not in terrain_syms:
                return (f"on_terrain for '{d.get('name')}' must be one of the terrain "
                        f"symbols {sorted(terrain_syms)}")
            if not isinstance(d.get("count"), int) or not (1 <= d["count"] <= 8):
                return "count must be an integer from 1 to 8"
        for d in objs:
            cells = d.get("cells", 1)
            if not isinstance(cells, int) or not (1 <= cells <= 4):
                return "cells must be an integer from 1 to 4"
        if len(objs) + len(ents) < 2:
            return "include at least 2 items in total"
        if len(objs) + len(ents) > 10:
            return "keep it to at most 10 items in total"
        return None

    obj = chat_json(llm, _prompt("items", place=place, terrain=desc), validate)
    items = []
    for d in obj["objects"]:
        items.append({**d, "kind": "object", "cells": d.get("cells", 1)})
    for d in obj["entities"]:
        items.append({**d, "kind": "entity", "walkable": True, "cells": 1})
    return items


def stage_coarse(llm: Llm, place: str, terrain: List[Dict], coarse_h: int,
                 coarse_w: int) -> List[str]:
    syms = [t["symbol"] for t in terrain]
    legend = ", ".join(f"{t['symbol']}={t['name']}" for t in terrain)

    def validate(obj):
        rows = obj.get("rows")
        if not isinstance(rows, list) or len(rows) != coarse_h:
            return f"rows must be a list of exactly {coarse_h} strings"
        for r in rows:
            if not isinstance(r, str) or len(r) != coarse_w:
                return f"every row must be a string of exactly {coarse_w} letters"
            bad = [ch for ch in r if ch not in syms]
            if bad:
                return f"row contains letters {bad} that are not terrain symbols {syms}"
        if len({ch for r in rows for ch in r}) < 2:
            return "use at least 2 different terrain types across the map"
        return None

    return chat_json(llm, _prompt("coarse", place=place, legend=legend, coarse_h=coarse_h,
                                  coarse_w=coarse_w, example_row=syms[0] * coarse_w,
                                  symbols=", ".join(syms)), validate)["rows"]


def upscale_and_smooth(rows: List[str], scale: int = SCALE) -> List[str]:
    grid = []
    for r in rows:
        row = "".join(ch * scale for ch in r)
        for _ in range(scale):
            grid.append(list(row))
    h, w = len(grid), len(grid[0])
    for _ in range(2):
        new = copy.deepcopy(grid)
        for r in range(h):
            for c in range(w):
                counts: Dict[str, int] = {}
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < h and 0 <= nc < w:
                            counts[grid[nr][nc]] = counts.get(grid[nr][nc], 0) + 1
                best = max(counts.items(), key=lambda kv: (kv[1], kv[0] == grid[r][c]))
                if best[1] >= 6:
                    new[r][c] = best[0]
        grid = new
    return ["".join(r) for r in grid]


def _grid_text(grid: List[str]) -> str:
    return "\n".join(f"{i:2d} {row}" for i, row in enumerate(grid))


def _valid_pt(p, h: int, w: int) -> bool:
    return (isinstance(p, list) and len(p) == 2 and all(isinstance(x, int) for x in p)
            and 0 <= p[0] < h and 0 <= p[1] < w)


def apply_ops(grid: List[str], ops: List[Dict]) -> List[str]:
    g = [list(r) for r in grid]
    for o in ops:
        t = o["tile"]
        if o["op"] == "line":
            (r0, c0), (r1, c1) = o["from"], o["to"]
            steps = max(abs(r1 - r0), abs(c1 - c0), 1)
            for i in range(steps + 1):
                g[round(r0 + (r1 - r0) * i / steps)][round(c0 + (c1 - c0) * i / steps)] = t
        else:
            (r0, c0), (r1, c1) = o["top_left"], o["bottom_right"]
            r0, r1 = sorted((r0, r1))
            c0, c1 = sorted((c0, c1))
            fill = o.get("fill", False)
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    if fill or r in (r0, r1) or c in (c0, c1):
                        g[r][c] = t
    return ["".join(r) for r in g]


def stage_fine(llm: Llm, place: str, terrain: List[Dict], grid: List[str]) -> List[str]:
    h, w = len(grid), len(grid[0])
    syms = {t["symbol"] for t in terrain}
    legend = ", ".join(f"{t['symbol']}={t['name']}" for t in terrain)

    def validate(obj):
        ops = obj.get("ops")
        if not isinstance(ops, list):
            return "reply needs an ops list"
        if len(ops) > 12:
            return "use at most 12 ops"
        for o in ops:
            if o.get("op") not in ("line", "rect"):
                return 'each op must be "line" or "rect"'
            if o.get("tile") not in syms:
                return f"tile must be one of the terrain symbols {sorted(syms)}"
            if o["op"] == "line":
                if not (_valid_pt(o.get("from"), h, w) and _valid_pt(o.get("to"), h, w)):
                    return (f"line needs from and to as [row, col] with row 0..{h - 1}, "
                            f"col 0..{w - 1}")
            elif not (_valid_pt(o.get("top_left"), h, w)
                      and _valid_pt(o.get("bottom_right"), h, w)):
                return (f"rect needs top_left and bottom_right as [row, col] with row "
                        f"0..{h - 1}, col 0..{w - 1}")
        return None

    ops = chat_json(llm, _prompt("fine", place=place, legend=legend, h=h, w=w,
                                 h_max=h - 1, w_max=w - 1, grid=_grid_text(grid),
                                 symbols=", ".join(sorted(syms))), validate)["ops"]
    return apply_ops(grid, ops)


def stage_place(llm: Llm, place: str, terrain: List[Dict], items: List[Dict],
                grid: List[str]) -> Tuple[List[Dict], int]:
    h, w = len(grid), len(grid[0])
    legend = ", ".join(f"{t['symbol']}={t['name']}" for t in terrain)
    want = [{"symbol": d["symbol"], "name": d["name"], "count": d["count"],
             "on_terrain": d["on_terrain"]} for d in items]
    expected = {d["symbol"]: d["count"] for d in items}
    on_terrain = {d["symbol"]: d["on_terrain"] for d in items}

    def validate(obj):
        pl = obj.get("placements")
        if not isinstance(pl, list):
            return "reply needs a placements list"
        counts: Dict[str, int] = {}
        seen = set()
        for p in pl:
            s = p.get("symbol")
            if s not in expected:
                return f"symbol '{s}' is not in the item list"
            r, c = p.get("row"), p.get("col")
            if not (isinstance(r, int) and isinstance(c, int) and 0 <= r < h and 0 <= c < w):
                return f"row must be 0..{h - 1} and col 0..{w - 1}"
            if (r, c) in seen:
                return f"two items share the cell [{r}, {c}]; use one item per cell"
            seen.add((r, c))
            counts[s] = counts.get(s, 0) + 1
        for s, n in expected.items():
            if counts.get(s, 0) != n:
                return f"place exactly {n} of '{s}' (you placed {counts.get(s, 0)})"
        return None

    placements = chat_json(llm, _prompt("placement", place=place, legend=legend, h=h, w=w,
                                        grid=_grid_text(grid), items=json.dumps(want)),
                           validate)["placements"]
    snapped = 0
    taken = {(p["row"], p["col"]) for p in placements}
    for p in placements:
        pref = on_terrain[p["symbol"]]
        if grid[p["row"]][p["col"]] == pref:
            continue
        best = None
        for r in range(h):
            for c in range(w):
                if grid[r][c] == pref and (r, c) not in taken:
                    d = abs(r - p["row"]) + abs(c - p["col"])
                    if best is None or d < best[0]:
                        best = (d, r, c)
        if best:
            taken.discard((p["row"], p["col"]))
            p["row"], p["col"] = best[1], best[2]
            taken.add((best[1], best[2]))
            snapped += 1
    return placements, snapped



def walkable_grid(grid: List[str], terrain: List[Dict],
                  items: List[Dict], placements: List[Dict]) -> List[str]:
    """Cell truth for the game: terrain walkability with item footprints stamped over it."""
    walk_syms = {t["symbol"] for t in terrain if t["walkable"]}
    walk_flag = {d["symbol"]: d.get("walkable", True) for d in items}
    cells_of = {d["symbol"]: d.get("cells", 1) for d in items}
    h, w = len(grid), len(grid[0])
    walk = [[grid[r][c] in walk_syms for c in range(w)] for r in range(h)]
    for p in placements:
        n = cells_of.get(p["symbol"], 1)
        flag = walk_flag.get(p["symbol"], True)
        for r in range(p["row"], min(p["row"] + n, h)):
            for c in range(p["col"], min(p["col"] + n, w)):
                walk[r][c] = flag
    return ["".join("1" if v else "0" for v in row) for row in walk]


def walkable_stats(walkable: List[str]) -> Dict:
    h, w = len(walkable), len(walkable[0])
    cells = [(r, c) for r in range(h) for c in range(w) if walkable[r][c] == "1"]
    if not cells:
        return {"walkable_frac": 0.0, "largest_component_frac": 0.0, "n_components": 0}
    seen = set()
    components = []
    for cell in cells:
        if cell in seen:
            continue
        comp = 0
        q = deque([cell])
        seen.add(cell)
        while q:
            r, c = q.popleft()
            comp += 1
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = r + dr, c + dc
                if (0 <= nr < h and 0 <= nc < w and (nr, nc) not in seen
                        and walkable[nr][nc] == "1"):
                    seen.add((nr, nc))
                    q.append((nr, nc))
        components.append(comp)
    components.sort(reverse=True)
    return {"walkable_frac": round(len(cells) / (h * w), 3),
            "largest_component_frac": round(components[0] / len(cells), 3),
            "n_components": len(components)}


def generate_layout(llm: Llm, place: str, width_cells: int = 32,
                    height_cells: int = 24) -> Dict:
    """The whole layout: terrain classes, item list, painted grid, placements.

    width/height are rounded down to multiples of SCALE — the coarse pass owns geography
    at 1/SCALE resolution and the deterministic upscale owns the rest.
    """
    w = max(SCALE * 4, width_cells - width_cells % SCALE)
    h = max(SCALE * 3, height_cells - height_cells % SCALE)
    terrain = stage_tileset(llm, place)
    items = stage_items(llm, place, terrain)
    coarse = stage_coarse(llm, place, terrain, h // SCALE, w // SCALE)
    grid = upscale_and_smooth(coarse)
    grid = stage_fine(llm, place, terrain, grid)
    placements, snapped = stage_place(llm, place, terrain, items, grid)
    walkable = walkable_grid(grid, terrain, items, placements)
    return {"place": place, "terrain": terrain, "items": items, "grid": grid,
            "placements": placements, "snapped": snapped, "walkable": walkable,
            "checks": walkable_stats(walkable)}
