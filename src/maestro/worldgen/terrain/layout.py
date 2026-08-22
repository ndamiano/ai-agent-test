"""The semantic layout map I_layout, and the region masks read back from it (§2.2.2).

"We instead generate a global semantic layout map I_layout from p_layout, using
distinct colors to encode predefined terrain categories. The layout map converts
textual descriptions of region categories, relative positions, adjacency
relationships, and coverage into a unified 2D spatial partition, which is
subsequently used for region-mask extraction, height-field generation, material
assignment, and asset scattering."

Everything downstream reads region membership from this image. Not from the
discs in p_layout — from the image. That is the point of the stage: circles are
an arrangement, and a world whose regions are circles looks like one.

The paper generates the map from p_layout — text to image — and that is what
`draw()` does. It matters more than it sounds. Editing an analytic disc map
instead, which is the obvious way to keep control of where things are, makes the
discs the structure: at any denoise the model bends their edges and returns one
contiguous blob per region, because one blob per region is what it was shown.
The paper's own maps are nothing like that. They interleave — several disjoint
patches of the same category scattered across the frame, the way real ground
cover actually falls.

So the arrangement is described rather than drawn, and the disc map survives
only as a spatial prior for reading membership back out. `draw_by_redraw()`
keeps the older behaviour for a world whose regions genuinely are one blob each.

Reading membership back out is the hard half. The model does not hold a palette
exactly: asked for four colours it returns ten, drifted in hue and lightness,
and it likes to invent a grey in the middle that belongs to nothing. So a pixel
is not matched to a colour by equality. Each pixel is scored in CIELAB against
every region's reference colour plus a spatial term from where the plan put that
region; the references are then re-estimated from the pixels that chose them and
everything is scored again. A drifted red is still nearest red, and the
re-estimate moves the reference onto the drift. A colour the model invented goes
to whichever region already surrounds it.

The masks that come out are soft and sum to one at every pixel — the m~_r of
eq. 6 — so the height field, the material blend and the scattering all share
exactly one partition of the world.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..backends import ImageModel
from .models import RegionLayout, TerrainCategory, TerrainPlan

# Distinct colours for the predefined terrain categories. Chosen to be far apart
# in hue AND lightness, because the readback scores in CIELAB and two categories
# that differ only in saturation are two categories the model will merge.
CATEGORY_COLOURS: dict[str, tuple[int, int, int]] = {
    "dune-field": (232, 168, 56),
    "sand-flat": (240, 214, 140),
    "hardpan": (176, 152, 120),
    "badlands": (156, 92, 60),
    "mesa": (196, 104, 72),
    "canyon": (124, 60, 52),
    "rocky-plateau": (148, 148, 156),
    "mountain": (104, 96, 112),
    "foothills": (128, 132, 96),
    "grassland": (132, 180, 84),
    "scrubland": (156, 164, 96),
    "forest": (56, 112, 64),
    "wetland": (72, 132, 124),
    "riverbed": (96, 148, 176),
    "lakebed": (72, 108, 152),
    "beach": (236, 224, 176),
    "cliff": (108, 104, 96),
    "snowfield": (240, 244, 248),
    "volcanic": (72, 64, 68),
    "crater-field": (120, 112, 104),
}

# Spare colours, used when two regions share a category and would otherwise be
# indistinguishable in the map. Membership is per region, not per category, so
# two regions the same colour is two regions that cannot be told apart.
SPARE_COLOURS: list[tuple[int, int, int]] = [
    (208, 72, 140), (96, 200, 208), (176, 96, 208), (208, 128, 64),
    (64, 160, 120), (200, 200, 72), (120, 88, 176), (224, 112, 112),
]

RESOLUTION = 768        # what the image model draws at, not the height-field grid
COLOUR_T = 22.0         # Lab distance costing as much as one disc radius
SPATIAL_W = 0.55        # weight of the plan's arrangement against the drawn colour
REFITS = 2              # re-estimate each region's colour, then score again
OUTLIER_T = 34.0        # Lab distance past which a pixel is no drawn colour at all
MIN_SHARE = 0.005       # a region below this share of the map means the read failed
DENOISE = 0.90          # how far the redraw may travel from the disc map

NEGATIVE = (
    "photograph, perspective, shading, gradient, texture, objects, text, "
    "watermark, new colours, blurry, soft focus, labels, legend, "
    # everything the map idioms drag in uninvited. A "fantasy game map" arrives
    # with rivers, a green surround and hatched relief; a "segmentation mask"
    # drifts photographic and comes back with shaded terrain. Both are colours
    # no region claims, and every one of them has to be grown back into a
    # neighbour at readback.
    "rivers, water, lakes, coastline, grass, green, vegetation, mountains, "
    "hills, relief shading, hatching, contour lines, outlines, black lines, "
    "roads, paths, borders, background, unfilled areas, compass, grid"
)


def assign_colours(plan: TerrainPlan) -> dict[str, tuple[int, int, int]]:
    """One colour per region, keyed by its category where that is unambiguous."""
    colours: dict[str, tuple[int, int, int]] = {}
    used: set[tuple[int, int, int]] = set()
    spares = iter(SPARE_COLOURS)
    for row in plan.layout:
        colour = CATEGORY_COLOURS.get(row.category, (128, 128, 128))
        if colour in used:
            colour = next(spares)
        used.add(colour)
        colours[row.region_id] = colour
    return colours


def disc_map(
    plan: TerrainPlan,
    colours: dict[str, tuple[int, int, int]],
    resolution: int = RESOLUTION,
) -> Image.Image:
    """The analytic arrangement: flat colour, hard edges, one owner per pixel.

    Flat and hard on purpose. This is the input to a redraw, and an image model
    shown a blurred boundary reproduces the blur rather than replacing it with a
    real edge.
    """
    weights = disc_weights(plan, resolution)
    owner = weights.argmax(axis=0)
    palette = np.array([colours[r.region_id] for r in plan.layout], np.uint8)
    return Image.fromarray(palette[owner])


def disc_weights(plan: TerrainPlan, resolution: int) -> np.ndarray:
    """Per-region disc membership, (regions, n, n), before any image is drawn.

    Also the spatial prior the readback leans on: cost 0 at a region's centre,
    rising through its radius. Softness comes from `falloff`, which is what the
    planner said about how definite that region's edge is.
    """
    yy, xx = np.mgrid[0:resolution, 0:resolution].astype(np.float32)
    xx = (xx + 0.5) / resolution
    yy = (yy + 0.5) / resolution
    out = []
    for row in plan.layout:
        cx, cy = row.center
        distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(row.radius, 1e-3)
        softness = max(row.falloff, 1e-3)
        out.append(np.exp(-((distance / softness) ** 2)).astype(np.float32))
    return np.stack(out)


def disc_owner(plan: TerrainPlan, resolution: int) -> np.ndarray:
    """Ownership from the arrangement alone, with containment respected.

    `disc_weights` cannot answer this. Its Gaussians peak at 1.0 at every
    centre, so where one region is drawn inside another -- which is a design the
    planner reaches for often, a wreck field in the middle of a plain, a town in
    a valley -- the wider disc wins every pixel including the smaller one's own
    core. Measured on the alien world: `graveyard-core`, planned for 35% of the
    map and concentric with a `jungle-plain` of radius 0.95, owned 0.0% of it.

    So ownership is decided by containment rather than by height. A pixel inside
    any disc goes to the smallest disc containing it, which is the painter's
    order -- the broad regions laid down first, the specific ones over the top.
    A pixel inside none goes to whichever disc it is least far outside, measured
    in that disc's own radii so a small region can still hold its surroundings.
    """
    yy, xx = np.mgrid[0:resolution, 0:resolution].astype(np.float32)
    xx = (xx + 0.5) / resolution
    yy = (yy + 0.5) / resolution
    radii = np.array([max(r.radius, 1e-3) for r in plan.layout], np.float32)
    reach = []
    for row, radius in zip(plan.layout, radii):
        cx, cy = row.center
        reach.append(np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius)
    reach = np.stack(reach)  # distance in each region's own radii

    inside = reach <= 1.0
    # inside a disc, the tightest disc wins; outside every disc, the nearest.
    # adding the radius keeps that a single argmin: it is below 1 for anything
    # contained and above 1 for anything that is not, so no outside pixel can
    # ever outrank an inside one.
    cost = np.where(inside, radii[:, None, None], 1.0 + reach)
    return cost.argmin(axis=0).astype(np.int32)


def instruction(plan: TerrainPlan, colours: dict[str, tuple[int, int, int]]) -> str:
    """The redraw prompt: same colours, same places, real boundaries.

    The swatches say WHERE each region is. They do not say what kind of place it
    is, and a model told only "wiggle these edges" wiggles them all the same
    way. Naming the categories is what makes a boundary between a plateau and a
    flat come back looking like an escarpment.

    The wording is stronger than it reads. Asked politely for "natural rather
    than circular" boundaries at a denoise of 0.62, this model returns the discs
    back with their arcs very slightly bent — a redraw that changed nothing. It
    takes both the explicit refusal of arcs and a denoise near 0.9 before real
    erosional spurs and inlets appear, and the colours survive that: they are
    restated every step, and the readback re-estimates them anyway.
    """
    swatches = "; ".join(
        f"{row.region_id.replace('-', ' ')} ({row.category.replace('-', ' ')}) = "
        f"RGB({colours[row.region_id][0]},{colours[row.region_id][1]},"
        f"{colours[row.region_id][2]})"
        for row in plan.layout
    )
    return (
        "This is a top-down map of a landscape, drawn as flat blocks of solid "
        f"colour. The coloured areas are: {swatches}. Redraw the same map with "
        "these exact colours in the same places, but make every boundary natural "
        "rather than circular: strongly irregular, jagged, lobed edges with deep "
        "inlets and protruding spurs, like a geological survey map. No arcs, no "
        "circles, no smooth curves. Every part of the square is one of "
        "those colours. Use ONLY those colours — no new colours, no in-between "
        "shades, no blending at the edges. Flat solid colours, no shading, no "
        "texture, no labels, top-down map."
    )


def _where(row: RegionLayout) -> str:
    """The plan's numeric placement, said the way a map illustrator would hear it."""
    x, y = row.center
    vertical = "northern" if y < 0.38 else "southern" if y > 0.62 else "central"
    horizontal = "western" if x < 0.38 else "eastern" if x > 0.62 else "central"
    if vertical == "central" and horizontal == "central":
        where = "the middle of the map"
    elif "central" in (vertical, horizontal):
        where = f"the {vertical if horizontal == 'central' else horizontal} part of the map"
    else:
        where = f"the {vertical}-{horizontal} part of the map"
    return f"around {where}, covering roughly {row.coverage * 100:.0f}% of it"


def description(plan: TerrainPlan, colours: dict[str, tuple[int, int, int]]) -> str:
    """The prompt for I_layout, built from p_layout.

    Everything p_layout holds goes in: category, colour, position, coverage and
    adjacency. Position is given in words rather than coordinates because that
    is what an image model acts on; the exact centres survive as the readback's
    spatial prior, which is where precision actually has to live.

    The idiom asked for is a land cover classification map, and it is load-bearing.
    "Stylised game map" produces the right shapes and the wrong everything else:
    rivers, a green border, hatched relief, an ornamental frame — a quarter of
    the frame in colours no region owns. "Segmentation mask" drifts photographic
    and comes back shaded. Land cover is the idiom that is natively flat,
    categorical, gapless and patchy, which is precisely what I_layout is.
    """
    parts = []
    for row in plan.layout:
        r, g, b = colours[row.region_id]
        neighbours = (
            f", bordering the {', '.join(n.replace('-', ' ') for n in row.adjacent_to)}"
            if row.adjacent_to else ""
        )
        parts.append(
            f"{row.category.replace('-', ' ')} in solid RGB({r},{g},{b}), "
            f"{_where(row)}{neighbours}"
        )
    areas = "; ".join(parts)
    return (
        "A land cover classification map, top-down, flat categorical colours "
        f"only. The classes are: {areas}. Each class appears as several separate "
        "irregular patches with winding organic outlines, interlocking with its "
        "neighbours rather than sitting as one round blob. The classes tile the "
        "entire square edge to edge with no gaps. Flat solid fills only — no "
        "shading, no gradients, no in-between shades, no blending at the edges, "
        "no outlines, no text, no labels, no grid, no border."
    )


def draw(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    resolution: int = RESOLUTION,
    seed: int = 17,
    overwrite: bool = False,
) -> Path:
    """Generate I_layout from p_layout, as §2.2.2 specifies. Returns its path.

    An existing map is kept unless `overwrite`. Every later stage indexes by the
    partition read out of this image, so redrawing it silently would invalidate
    masks, materials and scatter positions that were derived from the old one.

    The disc map is still rendered beside it as `layout_discs.png`. It is no
    longer an input to the drawing, but it is the arrangement the readback
    scores against, and it is the first thing to look at when a region ends up
    somewhere nobody asked for.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = out_dir / "layout.png"
    if existing.exists() and not overwrite:
        print(f"[layout] keeping the map already at {existing}")
        return existing
    colours = assign_colours(plan)
    disc_map(plan, colours, resolution).save(out_dir / "layout_discs.png")

    images = images or ImageModel()
    return images.generate(
        description(plan, colours),
        out_dir / "layout.png",
        negative=NEGATIVE,
        width=resolution,
        height=resolution,
        seed=seed,
    )


def draw_by_redraw(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    resolution: int = RESOLUTION,
    denoise: float = DENOISE,
    seed: int = 17,
) -> Path:
    """Redraw the analytic disc map instead of generating one.

    Keeps the planned arrangement almost exactly and pays for it in structure:
    what comes back is the discs with irregular edges, never interleaved
    patches. Worth having when a world's regions really are one contiguous area
    each — a single island, a single crater — and worth avoiding otherwise.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    colours = assign_colours(plan)

    discs = out_dir / "layout_discs.png"
    disc_map(plan, colours, resolution).save(discs)

    images = images or ImageModel()
    return images.edit(
        instruction(plan, colours),
        [discs],
        out_dir / "layout.png",
        negative=NEGATIVE,
        denoise=denoise,
        seed=seed,
    )


# -- reading membership back out ---------------------------------------------


def _to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in 0..1 to CIELAB (D65). Shape (..., 3) in, (..., 3) out.

    Lab rather than RGB because the scoring below asks "is this the same colour
    the model was given", and RGB distance answers a different question — it
    calls a dark red and a dark blue close, and a bright yellow and a slightly
    less bright yellow far apart.
    """
    a = np.asarray(rgb, np.float32)
    linear = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    matrix = np.array([[0.4124, 0.3576, 0.1805],
                       [0.2126, 0.7152, 0.0722],
                       [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = linear @ matrix.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.stack([116.0 * f[..., 1] - 16.0,
                     500.0 * (f[..., 0] - f[..., 1]),
                     200.0 * (f[..., 1] - f[..., 2])], -1)


def classify(
    plan: TerrainPlan,
    layout_png: Path | str,
    resolution: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Owner index per pixel, read from the drawn colours. Returns (owner, refs).

    Colour first, arrangement second. A pixel is scored against every region's
    reference colour in CIELAB, plus a spatial term for how far it sits from
    where that region was placed. The spatial term breaks ties and catches
    invented colours; it does not overrule a clear colour match, or the redraw
    would have been pointless.

    The references are then re-estimated as the mean colour of the pixels that
    chose them, twice. That is what absorbs hue drift: the model's idea of the
    orange it was given moves, and the reference follows it.
    """
    image = Image.open(layout_png).convert("RGB").resize(
        (resolution, resolution), Image.NEAREST
    )
    pixels = _to_lab(np.asarray(image, np.float32) / 255.0)

    colours = assign_colours(plan)
    refs = _to_lab(
        np.array([colours[r.region_id] for r in plan.layout], np.float32) / 255.0
    )
    spatial = 1.0 - disc_weights(plan, resolution)  # 0 at a centre, 1 far away

    owner = None
    for step in range(REFITS + 1):
        distance = np.linalg.norm(pixels[None, ...] - refs[:, None, None, :], axis=-1)
        cost = distance / COLOUR_T + SPATIAL_W * spatial
        owner = cost.argmin(axis=0)
        if step == REFITS:
            break
        for index in range(len(refs)):
            chosen = owner == index
            if chosen.sum() > 32:  # too few pixels is noise, not a drift estimate
                refs[index] = pixels[chosen].mean(axis=0)

    # A colour that is no region's, invented mid-redraw, is grown in from its
    # neighbours rather than handed to whichever disc happens to sit under it.
    distance = np.linalg.norm(pixels[None, ...] - refs[:, None, None, :], axis=-1)
    outlier = distance.min(axis=0) > OUTLIER_T
    if outlier.any():
        owner = _grow_into(owner, outlier)
    return owner, refs


def _grow_into(owner: np.ndarray, holes: np.ndarray, rounds: int = 64) -> np.ndarray:
    """Fill `holes` from whatever surrounds them, one ring at a time."""
    owner = owner.copy()
    owner[holes] = -1
    for _ in range(rounds):
        empty = owner < 0
        if not empty.any():
            break
        filled = owner.copy()
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            neighbour = np.roll(owner, shift, axis)
            take = empty & (neighbour >= 0) & (filled < 0)
            filled[take] = neighbour[take]
        if (filled == owner).all():
            break  # nothing left that touches a known pixel
        owner = filled
    owner[owner < 0] = 0
    return owner


def weights_from(
    plan: TerrainPlan,
    owner: np.ndarray,
    resolution: int,
) -> np.ndarray:
    """The soft normalised region weights m~_r of eq. 6, (regions, n, n).

    Hard ownership is blurred by the plan's own boundary_blend_m, converted from
    metres, and then normalised so the weights sum to one everywhere. Sharing
    one partition between the height field, the materials and the scattering is
    what stops a rock sitting on sand that is textured as rock.
    """
    blend_px = max(
        1.0, plan.world.boundary_blend_m / plan.world.size_m * resolution
    )
    masks = []
    for index in range(len(plan.layout)):
        hard = (owner == index).astype(np.float32)
        soft = Image.fromarray((hard * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(blend_px)
        )
        masks.append(np.asarray(soft, np.float32) / 255.0)
    weights = np.stack(masks)
    total = weights.sum(axis=0, keepdims=True)
    # every pixel belongs somewhere: where the blur has cancelled out, fall back
    # to hard ownership rather than dividing by nothing
    empty = total[0] < 1e-6
    if empty.any():
        for index in range(len(weights)):
            weights[index][empty] = (owner[empty] == index).astype(np.float32)
        total = weights.sum(axis=0, keepdims=True)
    return weights / np.maximum(total, 1e-6)


def read_back(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    layout_png: Path | str | None = None,
    resolution: int | None = None,
) -> np.ndarray:
    """Turn the drawn map into the shared region partition, and save it.

    Writes `layout_masks.npy` (the m~_r), `layout_owner.png` (hard ownership, in
    the reference colours, which is the picture to look at when something is in
    the wrong place) and `layout_keys.json` (which index is which region).

    A region that ends up with almost none of the map means the readback failed
    rather than that the model drew it small, so ownership falls back to the
    arrangement and says so.
    """
    out_dir = Path(out_dir)
    resolution = resolution or plan.world.heightmap_resolution
    layout_png = Path(layout_png or out_dir / "layout.png")

    owner, _ = classify(plan, layout_png, resolution)

    shares = [(owner == i).mean() for i in range(len(plan.layout))]
    missing = [i for i, share in enumerate(shares) if share < MIN_SHARE]
    if missing:
        # only the regions that failed to read back are re-seeded, and only over
        # the ground the arrangement gives them. Discarding the whole drawn map
        # because one region came back short throws away three good boundaries
        # to fix one bad one, and the drawn boundaries are the entire point of
        # having redrawn the discs.
        lost = [plan.layout[i].region_id for i in missing]
        print(
            f"[layout] {lost} almost absent from the drawn map — stamping them "
            f"back on from the planned arrangement"
        )
        arrangement = disc_owner(plan, resolution)
        for index in missing:
            owner = np.where(arrangement == index, index, owner)
        shares = [(owner == i).mean() for i in range(len(plan.layout))]

    for row, share in zip(plan.layout, shares):
        print(f"[layout] {row.region_id:14s} {share * 100:5.1f}% (planned {row.coverage * 100:4.1f}%)")

    weights = weights_from(plan, owner, resolution)
    np.save(out_dir / "layout_masks.npy", weights)

    colours = assign_colours(plan)
    palette = np.array([colours[r.region_id] for r in plan.layout], np.uint8)
    Image.fromarray(palette[owner]).save(out_dir / "layout_owner.png")
    (out_dir / "layout_keys.json").write_text(
        json.dumps(
            {
                "regions": [r.region_id for r in plan.layout],
                "categories": [r.category for r in plan.layout],
                "colours": {r.region_id: list(colours[r.region_id]) for r in plan.layout},
                "resolution": resolution,
                "share": {r.region_id: float(s) for r, s in zip(plan.layout, shares)},
            },
            indent=2,
        )
    )
    return weights


__all__ = [
    "assign_colours",
    "disc_map",
    "disc_weights",
    "instruction",
    "description",
    "draw",
    "draw_by_redraw",
    "classify",
    "weights_from",
    "read_back",
    "CATEGORY_COLOURS",
    "RESOLUTION",
]
