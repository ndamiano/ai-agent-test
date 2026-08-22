"""Schemas for stage 2a — the terrain planning agent (§2.2.1).

Stage 1 says what the world is. It does not say where anything sits, how high,
how rough, or what one repeat of a surface measures. The paper puts a second
agent in that gap, producing

    P_terrain = (p_layout, p_asset, p_material, theta_terrain)          (eq. 4)

and this module is that tuple, one class per component, because the paper is
explicit that P_terrain is an *interface*: terrain asset generation reads
p_layout and p_material, height-field construction reads theta_terrain, and
scattering reads p_asset. Keeping them apart keeps each stage's input honest.

Two rules the schemas enforce rather than hope for.

Closed vocabularies. A region's category and a landform's operator are chosen
from enums, never written free-hand, because every one of them has to become a
mask colour or a numpy expression downstream. An agent that invents "sandy
mountain-ish" has invented something nothing can build.

Metres, not multipliers. Elevations, wavelengths, amplitudes and feature sizes
are all in metres, so a validator can ask whether a 400 m dune wavelength fits
in a 300 m world. Frequency-as-cycles-across-the-map — the natural
implementation unit — is derivable from a wavelength and the world size, and is
not a thing an agent reasons about well.

Everything here is handed to `@tool` functions, so the docstrings and
`Field(description=...)` are prompt text, and a failed validator returns to the
model as a tool error it can act on.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The predefined terrain categories of §2.2.2: "distinct colors to encode
# predefined terrain categories". Predefined is the operative word — the layout
# map's palette is keyed by this enum, so a category that is not here has no
# colour, and a region painted in a colour nothing claims cannot be read back.
TerrainCategory = Literal[
    "dune-field",
    "sand-flat",
    "hardpan",
    "badlands",
    "mesa",
    "canyon",
    "rocky-plateau",
    "mountain",
    "foothills",
    "grassland",
    "scrubland",
    "forest",
    "wetland",
    "riverbed",
    "lakebed",
    "beach",
    "cliff",
    "snowfield",
    "volcanic",
    "crater-field",
]

# Geomorphic operators G_{r,j} of eq. 6. Each one has to exist as a function of
# position in the height-field builder, so this list is a contract with that
# code, not a description of terrain in general.
OperatorKind = Literal[
    "peak",      # isolated summits
    "ridge",     # a linear crest
    "dune",      # periodic windward/leeward asymmetry
    "terrace",   # quantised steps
    "crater",    # rim-and-bowl depressions
    "plateau",   # a raised flat with steep sides
    "valley",    # an incised trough
    "canyon",    # a deep, steep-walled cut
    "erosion",   # drainage-like smoothing and channelling
]

MaterialPathway = Literal["procedural", "generative"]

# How much structure a scattered thing has, which is what decides the triangle
# budget its mesh is reconstructed at. The planner says which of these a category
# is, because that is a fact about the object; the number of triangles that buys
# is the pipeline's business and lives in `worldclaw.terrain.assets`.
#
# A budget is needed at all because reconstruction has no opinion: TRELLIS
# returns the same ~50k triangles for a grass tuft as for a cathedral, and a
# world scatters those thousands of times. Undergrowth at 50k is what made a
# 600 m map cost 47M triangles.
AssetDetail = Literal["simple", "moderate", "intricate"]


class Strict(BaseModel):
    """Reject unknown keys instead of silently dropping them."""

    model_config = ConfigDict(extra="forbid")


# -- theta_terrain: the numbers the height field is built from ---------------


class NoiseBand(Strict):
    """One noise component N_{r,k} with its weight w_{r,k}, in metres.

    A region usually wants two or three: one long band for the shape of the
    land, one short band for surface texture. One band alone reads as either
    lumpy or featureless depending on which you pick.
    """

    wavelength_m: float = Field(
        gt=0,
        description="Distance between features of this band, in metres. Long "
        "wavelengths make broad swells; short ones make surface roughness.",
    )
    amplitude_m: float = Field(
        ge=0,
        description="How much height this band contributes, in metres, peak to trough.",
    )
    octaves: int = Field(
        default=1, ge=1, le=8,
        description="Detail doublings stacked on this band. 1 is smooth, 4 is rugged.",
    )


class GeomorphOp(Strict):
    """One geomorphic operator G_{r,j} with its weight alpha_{r,j}.

    `kind` picks the landform; `relief_m` is how much height it contributes.
    The remaining fields apply to some kinds and not others — give the ones
    that fit and leave the rest unset:

        peak, crater      count, feature_size_m
        ridge, dune       orientation_deg, feature_size_m
        terrace           steps
        plateau, valley,
        canyon            feature_size_m
        erosion           (relief_m alone; it removes height rather than adding)
    """

    kind: OperatorKind = Field(description="Which landform this operator makes.")
    relief_m: float = Field(
        ge=0,
        description="Height this operator contributes, in metres. This is its weight.",
    )
    count: int | None = Field(
        default=None, ge=1, le=200,
        description="How many discrete features, for peaks and craters.",
    )
    feature_size_m: float | None = Field(
        default=None, gt=0,
        description="Characteristic width of one feature, in metres.",
    )
    orientation_deg: float | None = Field(
        default=None, ge=0, lt=360,
        description="Direction the feature runs, for ridges and dunes. 0 is north.",
    )
    steps: int | None = Field(
        default=None, ge=2, le=40,
        description="Number of terrace steps.",
    )


class RegionTerrain(Strict):
    """theta_terrain for one region: base elevation, noise bands, operators.

    This is the bracketed term of eq. 6 for region r —

        h_r + sum_k w_rk N_rk(x) + sum_j alpha_rj G_rj(x)

    — before the region weights blend it with its neighbours.
    """

    region_id: str = Field(description="Id of the region, from the scene plan.")
    base_elevation_m: float = Field(
        description="h_r: the height this region sits at before any noise or "
        "landform, in metres. Differences between regions are what make a world "
        "read as layered rather than flat; a plateau above a basin is that "
        "difference, not an operator.",
    )
    noise: list[NoiseBand] = Field(
        default_factory=list,
        description="Noise bands for this region. Two or three is usual.",
    )
    operators: list[GeomorphOp] = Field(
        default_factory=list,
        description="Landform operators for this region. A region may have none — "
        "a flat pan is noise alone.",
    )


class WorldParams(Strict):
    """The parts of theta_terrain that belong to the world, not to one region."""

    size_m: float = Field(
        gt=0, le=8000,
        description="Width of the square world in metres. Take this from the "
        "scene plan's scale unless it is plainly wrong for what the world holds.",
    )
    sea_level_m: float = Field(
        description="Height of standing water, in metres. Put it below every "
        "region's lowest ground for a world with no water in it.",
    )
    boundary_blend_m: float = Field(
        gt=0,
        description="How wide the transition between two regions is, in metres. "
        "Narrow reads as a cliff or a shoreline; wide reads as a gradual change. "
        "A few percent of the world size is normal.",
    )
    heightmap_resolution: int = Field(
        default=1024, ge=256, le=4096,
        description="Height-field grid, in samples across. 1024 unless the world "
        "is unusually large.",
    )


# -- p_layout: where the regions are -----------------------------------------


class RegionLayout(Strict):
    """Where one region sits, as a soft disc over the unit square.

    The paper's p_layout is "region categories, relative positions, adjacency
    relationships, and approximate coverage". A centre, a radius and a falloff
    say all of that in a form the layout map can actually be drawn from, and
    §2.2.2 then replaces these circles with natural boundaries — this is the
    arrangement, not the final shape.

    Coordinates are fractions of the world: (0,0) is the north-west corner,
    (1,1) the south-east, so x runs east and y runs south.
    """

    region_id: str = Field(description="Id of the region, from the scene plan.")
    category: TerrainCategory = Field(
        description="Which predefined terrain category this region is. This "
        "chooses its colour on the layout map.",
    )
    center: tuple[float, float] = Field(
        description="Centre as (x, y), each 0 to 1. x east, y south.",
    )
    radius: float = Field(
        gt=0.02, le=1.0,
        description="Radius as a fraction of the world width.",
    )
    falloff: float = Field(
        ge=0.0, le=1.0,
        description="How soft the disc's edge is, 0 hard and 1 fully gradual. "
        "This is the arrangement's own softness; the metre-denominated "
        "boundary_blend_m is what the finished terrain uses.",
    )
    coverage: float = Field(
        gt=0, le=1,
        description="Roughly what fraction of the world this region should end "
        "up owning. These should sum to about 1 across all regions.",
    )
    adjacent_to: list[str] = Field(
        default_factory=list,
        description="Ids of regions this one borders.",
    )

    @model_validator(mode="after")
    def _check_center(self) -> RegionLayout:
        x, y = self.center
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(
                f"{self.region_id}: centre {self.center} is outside the world; "
                "x and y are fractions from 0 to 1"
            )
        return self


class TerrainAsset(Strict):
    """One category of terrain-bound object, scattered by region affinity.

    §2.2.2 is explicit about what belongs here: rocks, vegetation clusters,
    landform attachments — things that follow the ground and the ecology.
    Anything with a function, an identity, or a spatial relationship to
    something else is the regional stage's business, not this one's.
    """

    category: str = Field(
        description='ONE specimen, singular: "granite boulder", "pine tree", "dry scrub '
        'clump". Never a plural or a group — one mesh is made from it and scattered '
        'many times.',
    )
    appearance: str = Field(
        description="One sentence describing a single one of them, as a photograph of "
        "that one object alone would show it: its shape, colour and size. Say nothing "
        "about how many there are or how they stand together — per_hectare carries "
        "that. This becomes the prompt for its reference image.",
    )
    height_m: float = Field(
        gt=0,
        description="Typical height of one instance, in metres. Meshes come back "
        "normalised, so this is the only thing that gives them a real size.",
    )
    regions: list[str] = Field(
        min_length=1,
        description="Ids of the regions this scatters in.",
    )
    per_hectare: float = Field(
        gt=0,
        description="Target instances per hectare (100 m x 100 m) within those "
        "regions. A sparse desert rock is under 5; dense scrub is in the hundreds.",
    )
    max_slope_deg: float = Field(
        default=35.0, ge=0, le=90,
        description="Steepest ground this will sit on. Boulders tolerate more "
        "than trees.",
    )
    detail: AssetDetail = Field(
        default="moderate",
        description="How much fine structure the shape has, which sets how many "
        "triangles its mesh is built with. 'simple' is a closed blunt volume — a "
        "boulder, a cobble, a log. 'moderate' is a clump with some silhouette — "
        "scrub, a grass tuft, a reed bed. 'intricate' is branching or open "
        "structure that reads as its outline — a tree, a bare snag, a thorn "
        "bush. Choose by shape, not by size or importance: a huge smooth dune "
        "rock is still 'simple'.",
    )


# -- p_material: what the surfaces look like ---------------------------------


class RegionMaterial(Strict):
    """The surface of one region, and how it is to be made."""

    region_id: str = Field(description="Id of the region, from the scene plan.")
    surface: str = Field(
        description='What the ground is, e.g. "wind-rippled yellow sand".',
    )
    appearance: str = Field(
        description="Colour, roughness and fine structure in one or two "
        "sentences. This is the texture prompt.",
    )
    scale_m: float = Field(
        gt=0,
        description="Width of one repeat of this surface, in metres. Sand ripples "
        "repeat every metre or two; broken rock every five to ten. Getting this "
        "wrong is the most visible error in a finished terrain.",
    )
    pathway: MaterialPathway = Field(
        description="How to make it. 'generative' synthesises texture maps and "
        "suits complex or irregular surfaces; 'procedural' builds an adjustable "
        "node material and suits large uniform areas.",
    )


# -- P_terrain ---------------------------------------------------------------


class TerrainPlan(Strict):
    """P_terrain = (p_layout, p_asset, p_material, theta_terrain).

    The finished stage-2a artifact. Everything §2.2.2 and §2.2.3 need, and
    nothing they have to guess.
    """

    world: WorldParams
    layout: list[RegionLayout] = Field(min_length=1)
    terrain: list[RegionTerrain] = Field(min_length=1)
    materials: list[RegionMaterial] = Field(min_length=1)
    assets: list[TerrainAsset] = Field(default_factory=list)
    concept: str = Field(
        description="One paragraph describing the whole world as an aerial "
        "photograph of it would look. This is rendered as the concept image "
        "I_concept and conditions everything drawn later, so describe landforms, "
        "colours and light — not story, and not any single object.",
    )

    @model_validator(mode="after")
    def _check_references(self) -> TerrainPlan:
        ids = [r.region_id for r in self.layout]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate regions in layout: {dupes}")
        known = set(ids)

        for label, rows in (("terrain", self.terrain), ("materials", self.materials)):
            covered = [r.region_id for r in rows]
            if unknown := sorted(set(covered) - known):
                raise ValueError(f"{label} references regions not in layout: {unknown}")
            if missing := sorted(known - set(covered)):
                raise ValueError(f"regions with no {label} entry: {missing}")
            if dupes := sorted({r for r in covered if covered.count(r) > 1}):
                raise ValueError(f"more than one {label} entry for: {dupes}")

        for asset in self.assets:
            if unknown := sorted(set(asset.regions) - known):
                raise ValueError(
                    f"asset {asset.category!r} scatters in unknown regions: {unknown}"
                )

        for row in self.layout:
            if unknown := sorted(set(row.adjacent_to) - known):
                raise ValueError(
                    f"{row.region_id} is adjacent to unknown regions: {unknown}"
                )

        total = sum(r.coverage for r in self.layout)
        if not 0.7 <= total <= 1.4:
            raise ValueError(
                f"coverage should account for about the whole world; it sums to {total:.2f}"
            )

        # A wavelength longer than the world is a constant, and an operator wider
        # than the world is a tilt. Both are ways of asking for nothing.
        size = self.world.size_m
        for region in self.terrain:
            for band in region.noise:
                if band.wavelength_m > size * 2:
                    raise ValueError(
                        f"{region.region_id}: noise wavelength {band.wavelength_m:.0f} m "
                        f"exceeds twice the world size ({size:.0f} m) and would be flat"
                    )
            for op in region.operators:
                if op.feature_size_m and op.feature_size_m > size:
                    raise ValueError(
                        f"{region.region_id}: {op.kind} feature size "
                        f"{op.feature_size_m:.0f} m does not fit in a {size:.0f} m world"
                    )

        if self.world.boundary_blend_m > size * 0.5:
            raise ValueError(
                f"boundary_blend_m {self.world.boundary_blend_m:.0f} m is more than half "
                f"the world; regions would blend into one average"
            )
        return self

    # -- convenience for the stages downstream -------------------------------

    def region_ids(self) -> list[str]:
        return [r.region_id for r in self.layout]

    def layout_for(self, region_id: str) -> RegionLayout:
        return next(r for r in self.layout if r.region_id == region_id)

    def terrain_for(self, region_id: str) -> RegionTerrain:
        return next(r for r in self.terrain if r.region_id == region_id)

    def material_for(self, region_id: str) -> RegionMaterial:
        return next(r for r in self.materials if r.region_id == region_id)

    def lowest_ground_m(self) -> float:
        """The lowest the terrain plausibly reaches, for sanity-checking water."""
        return min(
            r.base_elevation_m
            - sum(b.amplitude_m for b in r.noise) / 2
            - sum(o.relief_m for o in r.operators if o.kind in ("canyon", "valley", "crater"))
            for r in self.terrain
        )


__all__ = [
    "TerrainPlan",
    "WorldParams",
    "RegionLayout",
    "RegionTerrain",
    "RegionMaterial",
    "TerrainAsset",
    "NoiseBand",
    "GeomorphOp",
    "TerrainCategory",
    "OperatorKind",
    "MaterialPathway",
]
