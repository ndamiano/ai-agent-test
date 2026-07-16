"""EXPERIMENT bridge: worldgen -> a game `world.ts` (terrain heightfield + streets/parcels + helpers)
the model authors on top of. No engine change beyond the `heightfield` shape. world.ts is a sibling
file main.ts imports; its API (spawnWorld/heightAt/WORLD) is stable so authored games keep working."""
import json
import math
import random
import sys
from pathlib import Path

import worldgen
from worldgen import noise, towns

CELL = 2.0  # world units per town cell

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
    ww, wh = town["ww"], town["wh"]
    ox, oz = ww * CELL / 2, wh * CELL / 2

    def wx(cx):
        return round((cx + 0.5) * CELL - ox, 3)

    def wz(cy):
        return round((cy + 0.5) * CELL - oz, 3)

    el = world["elevation"]
    EH, EW = len(el), len(el[0])
    cx0, cy0 = sett["x"], sett["y"]

    def raw_height(cx, cy):
        u, v = cx / max(1, ww - 1), cy / max(1, wh - 1)
        mx = min(EW - 1, max(0, int(cx0 - 4 + u * 8)))
        my = min(EH - 1, max(0, int(cy0 - 3 + v * 6)))
        macro = (el[my][mx] - 0.5) * 4.0                      # gentle rolling base (±2 units)
        detail = noise.fbm(cx / 5.0, cy / 5.0, seed + 4242, octaves=4) * 0.6  # organic wobble
        return macro + detail

    building_cells = {}
    for fid, f in town["footprints"].items():
        for yy in range(f["y"], f["y"] + f["h"]):
            for xx in range(f["x"], f["x"] + f["w"]):
                building_cells[(xx, yy)] = f.get("label", "town building")

    # height + color grids + a material grid, indexed [cy][cx]. The material grid drives the baked
    # texture (grass/path/stone); buildings sit ON GRASS (no dirt plot — stark plots looked awful).
    height = [[0.0] * ww for _ in range(wh)]
    color = [["#5f9a4c"] * ww for _ in range(wh)]
    material = [["grass"] * ww for _ in range(wh)]
    grass = []
    for cy in range(wh):
        for cx in range(ww):
            ch = rows[cy][cx]
            theme, role = legend[ch]["theme"], legend[ch]["role"]
            height[cy][cx] = round(raw_height(cx, cy), 3)
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

    # Scatter FOLIAGE + PROPS across the open ground — worldgen places these like it places the well,
    # so the world comes populated and the ground reads as a living field, not a flat mat.
    srng = random.Random(seed ^ 0x5EED)
    grass_points = []                       # grass tufts (rendered as one instanced grassfield)
    for gx, gz in grass:
        for _ in range(6):
            jx = gx + srng.uniform(-CELL / 2, CELL / 2)
            jz = gz + srng.uniform(-CELL / 2, CELL / 2)
            grass_points.append([round(jx, 2), round(height_at(jx, jz), 3), round(jz, 2)])
    props = []                              # bushes + rocks (sparse, as sphere entities)
    for i, (gx, gz) in enumerate(srng.sample(grass, min(34, len(grass)))):
        y = height_at(gx, gz)
        if i % 3 == 0:
            props.append({"x": round(gx, 2), "y": round(y + 0.18, 2), "z": round(gz, 2),
                          "r": round(srng.uniform(0.3, 0.55), 2), "color": "#70747a"})   # rock (mostly buried)
        else:
            props.append({"x": round(gx, 2), "y": round(y + 0.45, 2), "z": round(gz, 2),
                          "r": round(srng.uniform(0.5, 0.9), 2),
                          "color": srng.choice(["#3f7a3c", "#356f34", "#4a8a44"])})     # bush

    buildings = []
    for fid, f in town["footprints"].items():
        label = f.get("label", "town building")
        cxw = wx(f["x"] + f["w"] / 2 - 0.5)
        czw = wz(f["y"] + f["h"] / 2 - 0.5)
        buildings.append({
            "id": fid, "label": label, "mesh": label.replace(" ", "_"), "x": cxw, "z": czw,
            "w": round(f["w"] * CELL, 2), "d": round(f["h"] * CELL, 2),
            "h": BUILDING_H.get(label, 4.0),
            "hx": round(height_at(cxw, czw), 3),
            "color": LABEL_COLOR.get(label, "#a9824f"),
        })

    plaza = {"x": wx(town["plaza"][0]), "z": wz(town["plaza"][1]),
             "h": round(height_at(wx(town["plaza"][0]), wz(town["plaza"][1])), 3)}
    g = town["gates"][0]["cell"]
    gate = {"x": wx(g[0]), "z": wz(g[1])}

    data = {"cell": CELL, "gw": ww, "gh": wh, "seed": seed,
            "height": height, "color": color, "buildings": buildings,
            "grass": grass, "grass_points": grass_points, "props": props,
            "plaza": plaza, "gate": gate}

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_world_ts(out_dir / "world.ts", data)
    _bake_terrain(material, out_dir / "assets" / "terrain.png")
    _write_preview(out_dir.parent / "town_preview.png", data)
    print(f"seed={seed} town={ww}x{wh} buildings={len(buildings)} tufts={len(grass_points)} props={len(props)}")
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
    src = '''// GENERATED by worldgen — the static village: a terrain heightfield + labelled building parcels.
// main.ts authors gameplay ON TOP: call spawnWorld(state.world) in init, then place your player/NPCs/
// items using WORLD.buildings / WORLD.plaza / WORLD.grass and heightAt(x,z). Do NOT edit this file.
%s
export const WORLD: any = %s;

// Spawn the village: ONE terrain heightfield entity (the ground) + a labelled box per building. The
// building `label` (kind) is also its `mesh` id, so the asset stage can skin each kind.
export function spawnWorld(world: any[]): void {
  world.push({ shape: "heightfield", grid: WORLD.height, colors: WORLD.color, cell: WORLD.cell, texture: "assets/terrain.png" });
  world.push({ shape: "grassfield", points: WORLD.grass_points, h: 0.45, w: 0.12, base: "#2f6b32", tip: "#7cc257" });
  for (const p of WORLD.props)
    world.push({ shape: "sphere", x: p.x, y: p.y, z: p.z, r: p.r, color: p.color });
  for (const b of WORLD.buildings)
    world.push({ shape: "box", x: b.x, y: b.hx + b.h / 2, z: b.z, w: b.w, h: b.h, d: b.d,
                 color: b.color, type: "building", label: b.label, mesh: b.mesh });
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
''' % (_world_schema_comment(data), j)
    path.write_text(src, encoding="utf-8")


TILES_DIR = Path(__file__).resolve().parents[2] / "assets" / "tiles"
_MATERIAL_TILE = {"grass": "grass", "path": "dirt_path", "stone": "stone_tile"}


def _tiled(name: str, W: int, H: int, tile_px: int):
    """A seamless tile texture repeated to fill WxH — continuous ground, no per-cell grid."""
    from PIL import Image
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
    from PIL import Image, ImageFilter
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
    from PIL import Image, ImageDraw
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
