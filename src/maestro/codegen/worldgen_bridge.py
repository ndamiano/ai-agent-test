"""EXPERIMENT bridge: worldgen -> a game `world.ts` (terrain heightfield + streets/parcels + helpers)
the model authors on top of. No engine change beyond the `heightfield` shape. world.ts is a sibling
file main.ts imports; its API (spawnWorld/heightAt/WORLD) is stable so authored games keep working."""
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import worldgen
from worldgen import noise, towns

CELL = 2.0  # world units per town cell
RING = 22   # wilderness cells beyond the town on every side — the "leave the village" space

THEME_COLOR = {
    "cobbled plaza": "#b7b0a0", "worn path": "#977c50", "stone well": "#9aa0a8",
    "dense hedgerow": "#2f5a2b", "timber and plaster wall": "#c9b183",
    "grassland": "#5f9a4c", "forest": "#3f7a3c", "hill": "#7c8a4e", "beach": "#d8c98f",
    "mountain": "#8a8a8f", "ocean": "#2f6f9a",
}
LABEL_COLOR = {
    "timber house": "#b9713f", "cottage": "#c69a52", "storehouse": "#93805f",
    "market stall": "#bd4a4a", "workshop": "#5f7f92", "granary": "#cdb254",
    "stone well": "#9aa0a8", "town building": "#a9824f",
}
BUILDING_H = {"granary": 6.0, "storehouse": 5.0, "workshop": 4.0, "market stall": 3.0,
              "timber house": 4.5, "cottage": 3.5, "town building": 4.0, "stone well": 1.2}


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def _rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def _jitter(color, cx, cy, seed):
    # deterministic per-cell brightness jitter so a field of grass isn't a flat sheet
    n = noise.fbm(cx / 3.0, cy / 3.0, seed + 777, octaves=2)  # -1..1
    f = 1.0 + n * 0.10
    return _hex([v * f for v in _rgb(color)])


def build(recipe, out_dir: Path):
    world, seed = worldgen.generate_best(recipe, range(30))
    sett = next(s for s in world["sites"] if s["type"] == "settlement")
    town = towns.build_town(sett, [], world, seed)
    rows, legend = town["tiles"]["rows"], town["tiles"]["legend"]
    tw, th = town["ww"], town["wh"]              # the town grid
    ww, wh = tw + 2 * RING, th + 2 * RING        # full grid: town centered in a wilderness ring
    ox, oz = ww * CELL / 2, wh * CELL / 2

    def wx(cx):
        return round((cx + 0.5) * CELL - ox, 3)

    def wz(cy):
        return round((cy + 0.5) * CELL - oz, 3)

    def in_town(fx, fy):
        return RING <= fx < RING + tw and RING <= fy < RING + th

    el = world["elevation"]
    EH, EW = len(el), len(el[0])
    cx0, cy0 = sett["x"], sett["y"]

    def raw_height(fx, fy):
        cx, cy = fx - RING, fy - RING                          # town-relative; the ring extrapolates
        u, v = cx / max(1, tw - 1), cy / max(1, th - 1)
        mx = min(EW - 1, max(0, int(cx0 - 4 + u * 8)))
        my = min(EH - 1, max(0, int(cy0 - 3 + v * 6)))
        macro = (el[my][mx] - 0.5) * 4.0                      # gentle rolling base (±2 units)
        detail = noise.fbm(fx / 5.0, fy / 5.0, seed + 4242, octaves=4) * 0.6  # organic wobble
        # outside the walls the ground rolls harder, and the far rim lifts into hills so the world
        # reads as a valley you're inside — never a floating table edge
        dx = max(0, RING - fx, fx - (RING + tw - 1))
        dy = max(0, RING - fy, fy - (RING + th - 1))
        t = min(1.0, max(dx, dy) / RING)
        rough = noise.fbm(fx / 9.0, fy / 9.0, seed + 77, octaves=3) * 1.6 * t
        rim = (t ** 2.5) * 6.0
        return macro + detail + rough + rim

    building_cells = {}
    for fid, f in town["footprints"].items():
        for yy in range(f["y"], f["y"] + f["h"]):
            for xx in range(f["x"], f["x"] + f["w"]):
                building_cells[(xx + RING, yy + RING)] = f.get("label", "town building")

    # height + color grids + a material grid, indexed [cy][cx] over the FULL grid. The material grid
    # drives the baked texture (grass/path/stone); buildings sit ON GRASS (no dirt plot — stark plots
    # looked awful). Ring cells default to grass; forest/roads/POIs carve them below.
    height = [[0.0] * ww for _ in range(wh)]
    color = [["#5f9a4c"] * ww for _ in range(wh)]
    material = [["grass"] * ww for _ in range(wh)]
    grass = []
    for cy in range(wh):
        for cx in range(ww):
            height[cy][cx] = round(raw_height(cx, cy), 3)
            if not in_town(cx, cy):
                continue
            theme = legend[rows[cy - RING][cx - RING]]["theme"]
            color[cy][cx] = THEME_COLOR.get(theme, "#5f9a4c")   # flat fallback (used only if the texture is absent)
            mat = "path" if theme == "worn path" else "stone" if theme == "cobbled plaza" else "grass"
            material[cy][cx] = mat
            if (cx, cy) not in building_cells and mat == "grass":
                grass.append([wx(cx), wz(cy)])

    def height_at(x, z):
        gx = x / CELL + (ww - 1) / 2
        gz = z / CELL + (wh - 1) / 2
        x0, z0 = int(math.floor(gx)), int(math.floor(gz))
        x0 = max(0, min(ww - 2, x0)); z0 = max(0, min(wh - 2, z0))
        tx, tz = gx - x0, gz - z0
        h00, h10 = height[z0][x0], height[z0][x0 + 1]
        h01, h11 = height[z0 + 1][x0], height[z0 + 1][x0 + 1]
        return (h00 * (1 - tx) + h10 * tx) * (1 - tz) + (h01 * (1 - tx) + h11 * tx) * tz

    # ── the WILDERNESS: forest, POIs, and roads in the ring — the reason the world isn't one room ──
    margin = 3
    def near_town(fx, fy):
        return RING - margin <= fx < RING + tw + margin and RING - margin <= fy < RING + th + margin

    forest_cells = [(fx, fy) for fy in range(1, wh - 1) for fx in range(1, ww - 1)
                    if not near_town(fx, fy)
                    and noise.fbm(fx / 6.0, fy / 6.0, seed + 909, octaves=3) > 0.22]
    for fx, fy in forest_cells:
        color[fy][fx] = THEME_COLOR["forest"]

    # POIs: destinations OUT THERE (a cave, a ruin, a camp) fanned around the town, roughly opposite
    # and beside the gate heading, deep in the ring — quests get somewhere to point.
    g = town["gates"][0]["cell"]
    gx, gy = g[0] + RING, g[1] + RING
    cxc, cyc = ww / 2.0, wh / 2.0
    ga = math.atan2(gy - cyc, gx - cxc)
    prng = random.Random(seed ^ 0xB01)
    pois, poi_cells = [], []
    for i, (kind, label) in enumerate([("cave", "cave mouth"), ("ruins", "old ruin"), ("camp", "abandoned camp")]):
        a = ga + (0.0, -1.9, 2.1)[i] + prng.uniform(-0.25, 0.25)
        spot = None
        for da in (0.0, 0.35, -0.35, 0.7, -0.7):    # rotate off obstructions until a ring spot fits
            for r_frac in (0.88, 0.76, 0.64):
                r = (min(ww, wh) / 2.0 - 4) * r_frac
                fx = int(cxc + math.cos(a + da) * r)
                fy = int(cyc + math.sin(a + da) * r * 0.85)
                if 3 <= fx < ww - 3 and 3 <= fy < wh - 3 and not near_town(fx, fy):
                    spot = (fx, fy)
                    break
            if spot:
                break
        fx, fy = spot or (3, 3)                      # corner fallback — always in the ring
        poi_cells.append((fx, fy))
        pois.append({"id": kind, "kind": kind, "label": label, "x": wx(fx), "z": wz(fy)})

    # Roads: worn paths from the gate out to the first two POIs, and onward past the first to the
    # map edge (the world implies it continues). Painted into the material grid like town streets.
    road = []
    def carve_road(x0, y0, x1, y1):
        steps = max(2, int(max(abs(x1 - x0), abs(y1 - y0)) * 2))
        for s in range(steps + 1):
            t = s / steps
            fx = int(round(x0 + (x1 - x0) * t + noise.fbm(t * 7.0, 0.3, seed + 55, octaves=2) * 2.2))
            fy = int(round(y0 + (y1 - y0) * t + noise.fbm(0.7, t * 7.0, seed + 56, octaves=2) * 2.2))
            for dx, dy in ((0, 0), (1, 0), (0, 1)):
                xx, yy = fx + dx, fy + dy
                if 0 <= xx < ww and 0 <= yy < wh and not in_town(xx, yy) and (xx, yy) not in building_cells:
                    material[yy][xx] = "path"
                    color[yy][xx] = THEME_COLOR["worn path"]
            if s % 4 == 0:
                road.append([wx(fx), wz(fy)])

    carve_road(gx, gy, *poi_cells[0])
    carve_road(gx, gy, *poi_cells[1])
    ex = max(2, min(ww - 3, int(cxc + math.cos(ga) * ww)))   # past POI 0 toward the rim
    ey = max(2, min(wh - 3, int(cyc + math.sin(ga) * wh)))
    carve_road(*poi_cells[0], ex, ey)

    # Trees: fill the forest cells (skip roads and POI clearings). Exported as [x, z, scale] — the
    # ~4KB form; spawnWorld builds each as trunk box + foliage sphere on the terrain.
    trng = random.Random(seed ^ 0x7EE5)
    trees = []
    for fx, fy in forest_cells:
        if material[fy][fx] != "grass":
            continue
        if any(abs(fx - px) < 3 and abs(fy - py) < 3 for px, py in poi_cells):
            continue
        if trng.random() > 0.5:
            continue
        jx = wx(fx) + trng.uniform(-0.8, 0.8)
        jz = wz(fy) + trng.uniform(-0.8, 0.8)
        trees.append([round(jx, 2), round(jz, 2), round(trng.uniform(0.8, 1.5), 2)])
    if len(trees) > 160:
        trees = trees[:: len(trees) // 160 + 1]

    # Regions: named spawn areas for gameplay ("the beast lairs in the forest") — sampled clear
    # points, not exhaustive cell lists.
    rrng = random.Random(seed ^ 0x4E64)
    ring_grass = [(fx, fy) for fy in range(1, wh - 1) for fx in range(1, ww - 1)
                  if not near_town(fx, fy) and material[fy][fx] == "grass"]
    fset = set(forest_cells)
    fpts = [c for c in ring_grass if c in fset]
    mpts = [c for c in ring_grass if c not in fset]
    regions = {"forest": [[wx(fx), wz(fy)] for fx, fy in rrng.sample(fpts, min(50, len(fpts)))],
               "meadow": [[wx(fx), wz(fy)] for fx, fy in rrng.sample(mpts, min(50, len(mpts)))]}

    # POI set dressing: a few primitives per site (tagged with a mesh id so the asset stage can skin
    # them), heights pre-resolved onto the terrain.
    def part(shape, x, z, lift, **kw):
        return {"shape": shape, "x": round(x, 2), "z": round(z, 2),
                "y": round(height_at(x, z) + lift, 2), **kw}
    for p in pois:
        x, z = p["x"], p["z"]
        if p["kind"] == "cave":
            p["parts"] = [
                part("sphere", x - 1.7, z - 1.2, 0.8, r=2.3, color="#6d7076"),
                part("sphere", x + 1.9, z - 1.5, 0.5, r=1.6, color="#7a7d84"),
                part("box", x, z, 1.1, w=2.4, h=2.2, d=2.0, color="#15171c", mesh="cave_mouth", label="cave mouth"),
            ]
        elif p["kind"] == "ruins":
            p["parts"] = [
                part("box", x - 2.2, z, 0.9, w=0.7, h=1.8, d=4.2, color="#8a8177", mesh="ruin_wall", label="ruined wall"),
                part("box", x + 2.3, z - 0.4, 0.6, w=0.7, h=1.2, d=3.4, color="#938a80", mesh="ruin_wall", label="ruined wall"),
                part("box", x + 0.2, z - 2.4, 0.7, w=3.8, h=1.5, d=0.7, color="#857c72", mesh="ruin_wall", label="ruined wall"),
                part("box", x - 0.4, z + 2.2, 0.4, w=2.6, h=0.8, d=0.7, color="#9a9187", mesh="ruin_wall", label="ruined wall"),
            ]
        else:   # camp
            p["parts"] = [
                part("box", x, z, 0.8, w=2.0, h=1.6, d=2.2, color="#b0703c", mesh="tent", label="tent"),
                part("box", x + 2.1, z + 0.8, 0.25, w=1.6, h=0.5, d=0.5, color="#6c4a2a", label="log"),
                part("sphere", x - 1.9, z + 1.4, 0.22, r=0.25, color="#ff7a2a", label="campfire"),
                part("sphere", x - 2.3, z + 1.1, 0.15, r=0.22, color="#5b5e63"),
                part("sphere", x - 1.5, z + 1.7, 0.15, r=0.22, color="#5b5e63"),
            ]

    # Scatter FOLIAGE + PROPS across the open ground — worldgen places these like it places the well,
    # so the world comes populated and the ground reads as a living field, not a flat mat.
    srng = random.Random(seed ^ 0x5EED)
    grass_points = []                       # grass tufts (rendered as one instanced grassfield)
    for gx2, gz2 in grass:
        for _ in range(6):
            jx = gx2 + srng.uniform(-CELL / 2, CELL / 2)
            jz = gz2 + srng.uniform(-CELL / 2, CELL / 2)
            grass_points.append([round(jx, 2), round(height_at(jx, jz), 3), round(jz, 2)])
    for fx, fy in srng.sample(ring_grass, min(1300, len(ring_grass))):   # sparser tufts in the wild
        for _ in range(2):
            jx = wx(fx) + srng.uniform(-CELL / 2, CELL / 2)
            jz = wz(fy) + srng.uniform(-CELL / 2, CELL / 2)
            grass_points.append([round(jx, 2), round(height_at(jx, jz), 3), round(jz, 2)])
    props = []                              # bushes + rock variants (spheres, plus slab boxes)
    wild = [[wx(fx), wz(fy)] for fx, fy in rrng.sample(mpts, min(40, len(mpts)))]
    for i, (gx2, gz2) in enumerate(srng.sample(grass, min(34, len(grass))) + wild):
        y = height_at(gx2, gz2)
        kind = i % 6
        # each prop carries a mesh id — the asset stage's GLB replaces the primitive in place,
        # keeping the wilderness in the same rendered style as the buildings
        if kind == 0:                       # buried round rock
            props.append({"x": round(gx2, 2), "y": round(y + 0.18, 2), "z": round(gz2, 2),
                          "r": round(srng.uniform(0.3, 0.55), 2), "color": "#70747a", "mesh": "rock"})
        elif kind == 1:                     # angular slab, randomly turned
            w = round(srng.uniform(0.7, 1.3), 2)
            h = round(srng.uniform(0.35, 0.7), 2)
            props.append({"x": round(gx2, 2), "y": round(y + h * 0.35, 2), "z": round(gz2, 2),
                          "w": w, "h": h, "d": round(w * srng.uniform(0.5, 0.8), 2),
                          "ry": round(srng.uniform(0, 3.1), 2), "mesh": "rock_slab",
                          "color": srng.choice(["#7b7f86", "#666a70", "#8a8d92"])})
        elif kind == 2:                     # boulder pair (same rock mesh at two scales)
            r = round(srng.uniform(0.5, 0.8), 2)
            props.append({"x": round(gx2, 2), "y": round(y + r * 0.55, 2), "z": round(gz2, 2),
                          "r": r, "color": "#75797f", "mesh": "rock"})
            props.append({"x": round(gx2 + r, 2), "y": round(y + r * 0.3, 2), "z": round(gz2 + r * 0.6, 2),
                          "r": round(r * 0.55, 2), "color": "#83868c", "mesh": "rock"})
        else:                               # bush
            props.append({"x": round(gx2, 2), "y": round(y + 0.45, 2), "z": round(gz2, 2),
                          "r": round(srng.uniform(0.5, 0.9), 2), "mesh": "bush",
                          "color": srng.choice(["#3f7a3c", "#356f34", "#4a8a44"])})

    buildings = []
    for fid, f in town["footprints"].items():
        label = f.get("label", "town building")
        cxw = wx(f["x"] + RING + f["w"] / 2 - 0.5)
        czw = wz(f["y"] + RING + f["h"] / 2 - 0.5)
        buildings.append({
            "id": fid, "label": label, "mesh": label.replace(" ", "_"), "x": cxw, "z": czw,
            "w": round(f["w"] * CELL, 2), "d": round(f["h"] * CELL, 2),
            "h": BUILDING_H.get(label, 4.0),
            "hx": round(height_at(cxw, czw), 3),
            "color": LABEL_COLOR.get(label, "#a9824f"),
        })

    # The plaza point is the SPAWN — the well parcel sits at the town's center, so nudge to the
    # nearest open cell (not a building footprint or its 8-neighbourhood) so nothing wedges the player.
    pcx, pcy = town["plaza"][0] + RING, town["plaza"][1] + RING
    def blocked(fx, fy):
        return any((fx + dx, fy + dy) in building_cells for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    if blocked(pcx, pcy):
        for radius in (2, 3, 4):
            open_cells = [(pcx + dx, pcy + dy) for dx in range(-radius, radius + 1)
                          for dy in range(-radius, radius + 1)
                          if in_town(pcx + dx, pcy + dy) and not blocked(pcx + dx, pcy + dy)]
            if open_cells:
                pcx, pcy = min(open_cells, key=lambda c: abs(c[0] - pcx) + abs(c[1] - pcy))
                break
    plaza = {"x": wx(pcx), "z": wz(pcy)}
    plaza["h"] = round(height_at(plaza["x"], plaza["z"]), 3)
    gate = {"x": wx(gx), "z": wz(gy)}

    data = {"cell": CELL, "gw": ww, "gh": wh, "seed": seed,
            "height": height, "color": color, "buildings": buildings,
            "grass": grass, "grass_points": grass_points, "props": props,
            "trees": trees, "pois": pois, "road": road, "regions": regions,
            "plaza": plaza, "gate": gate}

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_world_ts(out_dir / "world.ts", data)
    _bake_terrain(material, out_dir / "assets" / "terrain.png")
    _write_preview(out_dir.parent / "town_preview.png", data)
    print(f"seed={seed} world={ww}x{wh} (town {tw}x{th}) buildings={len(buildings)} trees={len(trees)} "
          f"pois={len(pois)} tufts={len(grass_points)} props={len(props)}")
    return data


def _ts_scalar(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    return "any"


def _ts_shape(v) -> str:
    """A one-line TS-ish shape for a WORLD value: object → its keys, array → element shape, with the
    terrain grids collapsed to `T[][]` and coordinate tuples kept literal (`[x,z]`)."""
    if isinstance(v, dict):
        return "{" + ", ".join(v.keys()) + "}"
    if isinstance(v, list) and v:
        e = v[0]
        if isinstance(e, dict):
            return "{" + ", ".join(e.keys()) + "}[]"
        if isinstance(e, list):
            if len(e) > 4:  # a heightfield row, not a coordinate tuple
                return _ts_scalar(e[0]) + "[][]"
            return "[" + ", ".join(_ts_scalar(x) for x in e) + "][]"
        return _ts_scalar(e) + "[]"
    if isinstance(v, list):
        return "any[]"
    return _ts_scalar(v)


def _world_schema_comment(data: dict) -> str:
    fields = "\n".join(f"//   {k}: {_ts_shape(v)}" for k, v in data.items())
    return (
        "// WORLD shape — the ONLY fields that exist (the literal below is elided in tool reads; spawn\n"
        "// from THESE names, a wrong name is `undefined`):\n"
        + fields
    )


def _write_world_ts(path: Path, data: dict):
    j = json.dumps(data, separators=(",", ":"))
    src = '''// GENERATED by worldgen — the static WORLD: a village inside a wilderness (forest, roads, POIs),
// all on one terrain heightfield. main.ts authors gameplay ON TOP: call spawnWorld(state.world) in
// init, then place your player/NPCs/items using WORLD.buildings / WORLD.plaza / WORLD.grass /
// WORLD.pois / WORLD.regions and heightAt(x,z). Do NOT edit this file.
__SCHEMA__
export const WORLD: any = __JSON__;

// Spawn the world: ONE terrain heightfield entity (the ground), the village buildings, the forest,
// and each POI's set dressing. A building's `label` (kind) is also its `mesh` id, so the asset
// stage can skin each kind; POI parts carry mesh ids too.
export function spawnWorld(world: any[]): void {
  world.push({ shape: "heightfield", grid: WORLD.height, colors: WORLD.color, cell: WORLD.cell, texture: "assets/terrain.png" });
  world.push({ shape: "grassfield", points: WORLD.grass_points, h: 0.45, w: 0.12, base: "#2f6b32", tip: "#7cc257" });
  for (const p of WORLD.props)
    p.w ? world.push({ shape: "box", x: p.x, y: p.y, z: p.z, w: p.w, h: p.h, d: p.d, ry: p.ry || 0, color: p.color, mesh: p.mesh })
        : world.push({ shape: "sphere", x: p.x, y: p.y, z: p.z, r: p.r, color: p.color, mesh: p.mesh });
  for (const b of WORLD.buildings)
    world.push({ shape: "box", x: b.x, y: b.hx + b.h / 2, z: b.z, w: b.w, h: b.h, d: b.d,
                 color: b.color, type: "building", label: b.label, mesh: b.mesh });
  // [x, z, scale] → one of four tree forms (deterministic from position, so the forest is varied
  // but stable): broadleaf, conifer, tall-slim, pale birch. Each tree is BOTH a primitive compound
  // (the unskinned fallback, hidden once its mesh loads) AND one skinOnly anchor the asset stage's
  // GLB renders through — so a skinned world's trees match the buildings' style.
  const TREE_FORMS = ["tree_broadleaf", "tree_conifer", "tree_slim", "tree_birch"];
  for (const t of WORLD.trees) {
    const y = heightAt(t[0], t[1]), s = t[2];
    const v = (Math.abs(Math.round(t[0] * 13.7 + t[1] * 7.3)) | 0) % 4;
    const id = TREE_FORMS[v];
    const trunk = (h: number, w: number, color: string) =>
      world.push({ shape: "box", x: t[0], y: y + h / 2, z: t[1], w, h, d: w, color, type: "tree", hideIfSkinned: id });
    const puff = (r: number, py: number, color: string, ox = 0, oz = 0) =>
      world.push({ shape: "sphere", x: t[0] + ox, y: y + py, z: t[1] + oz, r, color, type: "tree", hideIfSkinned: id });
    if (v === 0) {        // broadleaf
      trunk(1.8 * s, 0.34 * s, "#6c4a2a");
      puff(1.15 * s, 2.2 * s, "#3c7a38");
      world.push({ shape: "box", skinOnly: true, mesh: "tree_broadleaf", x: t[0], y: y + 1.7 * s, z: t[1], w: 2.4 * s, h: 3.4 * s, d: 2.4 * s, color: "#3c7a38", type: "tree" });
    } else if (v === 1) { // conifer: stacked, darkening upward
      trunk(1.3 * s, 0.3 * s, "#5c3f24");
      puff(0.95 * s, 1.7 * s, "#2c5f2e");
      puff(0.7 * s, 2.5 * s, "#2a582b");
      puff(0.45 * s, 3.1 * s, "#275227");
      world.push({ shape: "box", skinOnly: true, mesh: "tree_conifer", x: t[0], y: y + 1.8 * s, z: t[1], w: 2.0 * s, h: 3.6 * s, d: 2.0 * s, color: "#2c5f2e", type: "tree" });
    } else if (v === 2) { // tall and slim
      trunk(2.6 * s, 0.26 * s, "#7a5533");
      puff(0.8 * s, 3.0 * s, "#4c8f3f");
      world.push({ shape: "box", skinOnly: true, mesh: "tree_slim", x: t[0], y: y + 1.9 * s, z: t[1], w: 1.7 * s, h: 3.8 * s, d: 1.7 * s, color: "#4c8f3f", type: "tree" });
    } else {              // birch-ish: pale trunk, twin light puffs
      trunk(2.0 * s, 0.24 * s, "#c9c2ae");
      puff(0.7 * s, 2.4 * s, "#7ba14b");
      puff(0.5 * s, 1.9 * s, "#86ad55", 0.5 * s, 0.3 * s);
      world.push({ shape: "box", skinOnly: true, mesh: "tree_birch", x: t[0], y: y + 1.5 * s, z: t[1], w: 1.6 * s, h: 3.0 * s, d: 1.6 * s, color: "#7ba14b", type: "tree" });
    }
  }
  for (const p of WORLD.pois)
    for (const s of p.parts)
      world.push({ type: "poi", poi: p.id, ...s });
}

// Bilinear ground height at world (x,z). Put every entity ON the ground with this. ALWAYS returns a
// finite number — it clamps (x,z) to the grid. It returns NaN ONLY when x or z is itself NaN/undefined,
// so a `y=NaN` spawn means the coord you passed was undefined (a wrong WORLD field) — fix it in main.ts.
export function heightAt(x: number, z: number): number {
  const W = WORLD, gw = W.gw, gh = W.gh, cell = W.cell;
  let gx = x / cell + (gw - 1) / 2, gz = z / cell + (gh - 1) / 2;
  let x0 = Math.max(0, Math.min(gw - 2, Math.floor(gx)));
  let z0 = Math.max(0, Math.min(gh - 2, Math.floor(gz)));
  const tx = gx - x0, tz = gz - z0;
  const h = W.height;
  const a = h[z0][x0] * (1 - tx) + h[z0][x0 + 1] * tx;
  const b = h[z0 + 1][x0] * (1 - tx) + h[z0 + 1][x0 + 1] * tx;
  return a * (1 - tz) + b * tz;
}
'''.replace("__SCHEMA__", _world_schema_comment(data)).replace("__JSON__", j)
    path.write_text(src, encoding="utf-8")


TILES_DIR = Path(__file__).resolve().parents[2] / "assets" / "tiles"
_MATERIAL_TILE = {"grass": "grass", "path": "dirt_path", "stone": "stone_tile"}


def _tiled(name: str, W: int, H: int, tile_px: int):
    """A seamless tile texture repeated to fill WxH — continuous ground, no per-cell grid."""
    t = Image.open(TILES_DIR / f"{name}.png").convert("RGB").resize((tile_px, tile_px))
    base = Image.new("RGB", (W, H))
    for y in range(0, H, tile_px):
        for x in range(0, W, tile_px):
            base.paste(t, (x, y))
    return base


def _bake_terrain(material_grid, path: Path, cell_px: int = 22):
    """The ground texture the runtime maps onto the terrain, composited from the seamless painted tile
    set (assets/tiles). Grass fills everything continuously; dirt paths and the stone plaza are laid in
    with FEATHERED masks (blurred cell masks → organic edges, not axis-aligned tile squares). This reads
    like a hand-painted village ground and matches the buildings, instead of a flat-color checkerboard."""
    gh, gw = len(material_grid), len(material_grid[0])
    W, H = gw * cell_px, gh * cell_px
    tile_px = cell_px * 5                                   # one tile spans ~5 cells → visible detail, few repeats
    img = _tiled(_MATERIAL_TILE["grass"], W, H, tile_px)
    flat = img.resize((1, 1)).resize((W, H))               # its mean color
    img = Image.blend(img, flat, 0.5)                       # quiet the base — the scattered tufts carry the detail now

    def lay(mat: str, blur: float):
        present = any(mat in row for row in material_grid)
        if not present:
            return
        m = Image.new("L", (gw, gh), 0)
        px = m.load()
        for cy in range(gh):
            for cx in range(gw):
                if material_grid[cy][cx] == mat:
                    px[cx, cy] = 255
        m = m.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(blur))
        img.paste(_tiled(_MATERIAL_TILE[mat], W, H, tile_px), (0, 0), m)

    lay("path", cell_px * 0.5)                              # worn dirt trails, soft edges
    lay("stone", cell_px * 0.32)                            # plaza, a touch crisper
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _write_preview(path: Path, data):
    gw, gh, cell = data["gw"], data["gh"], data["cell"]
    S = 14
    img = Image.new("RGB", (gw * S, gh * S), "#20252b")
    d = ImageDraw.Draw(img)
    for cy in range(gh):
        for cx in range(gw):
            d.rectangle([cx * S, cy * S, cx * S + S, cy * S + S], fill=data["color"][cy][cx])

    def c2px(x, z):
        return (x / cell + gw / 2) * S, (z / cell + gh / 2) * S
    for b in data["buildings"]:
        px, pz = c2px(b["x"], b["z"]); hw = b["w"] / cell * S / 2; hd = b["d"] / cell * S / 2
        d.rectangle([px - hw, pz - hd, px + hw, pz + hd], fill=b["color"], outline="#241a10", width=2)
    pl = data["plaza"]; px, py = c2px(pl["x"], pl["z"])
    d.ellipse([px - 9, py - 9, px + 9, py + 9], outline="#fff", width=2)
    img.save(path)


if __name__ == "__main__":
    recipe = {"archetype": "continent", "size": "small",
              "palette": {"biomes": ["ocean", "beach", "grassland", "forest", "hill", "mountain"]},
              "locations": [{"id": "rivervale", "type": "settlement", "name": "Rivervale"}]}
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/wg_out/game")
    build(recipe, out)
