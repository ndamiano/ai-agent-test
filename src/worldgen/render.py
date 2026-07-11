_MARKER_POOL = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CHAR_POOL = "abcdefghijklmnopqrstuvwxyz0123456789"


def _marker_for(index):
    if index < len(_MARKER_POOL):
        return _MARKER_POOL[index]
    letter = _MARKER_POOL[index % len(_MARKER_POOL)]
    return letter * (1 + index // len(_MARKER_POOL))


def render_ascii(world: dict) -> str:
    w, h = world["size"]["w"], world["size"]["h"]
    biome_grid = world["biome"]

    biomes_present = sorted({biome_grid[y][x] for y in range(h) for x in range(w)})
    biome_char = {}
    pool_idx = 0
    for name in biomes_present:
        biome_char[name] = _CHAR_POOL[pool_idx % len(_CHAR_POOL)]
        pool_idx += 1

    road_cells = set()
    for path in world["roads"]:
        for x, y in path:
            road_cells.add((x, y))

    markers = {site["id"]: _marker_for(i) for i, site in enumerate(world["sites"])}

    site_cell_marker = {}
    for site in world["sites"]:
        if site.get("host"):
            continue
        marker = markers[site["id"]]
        cells = site.get("cells") or ([(site["x"], site["y"])] if site.get("x") is not None else [])
        for c in cells:
            cx, cy = c
            site_cell_marker[(cx, cy)] = marker

    rows = []
    for y in range(h):
        chars = []
        for x in range(w):
            if (x, y) in site_cell_marker:
                chars.append(site_cell_marker[(x, y)])
            elif (x, y) in road_cells:
                chars.append(".")
            else:
                chars.append(biome_char[biome_grid[y][x]])
        rows.append("".join(chars))

    out = [f"worldgen map {w}x{h}", ""]
    out.extend(rows)
    out.append("")
    out.append("Sites:")
    for i, site in enumerate(world["sites"]):
        marker = markers[site["id"]]
        if site.get("host"):
            out.append(f"  {marker} = {site['id']} (interior, host={site['host']})")
        else:
            out.append(f"  {marker} = {site['id']} ({site['type']}) @ ({site['x']},{site['y']})")

    out.append("")
    out.append("Biomes:")
    for name in biomes_present:
        out.append(f"  {biome_char[name]} = {name}")
    out.append("")
    out.append("Roads: '.'")

    return "\n".join(out)
