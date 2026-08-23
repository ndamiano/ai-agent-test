"""Schemas for stage 1 — intent analysis, then scene-level planning.

Stage 1 runs as two agents with two schemas, and the split is the point:

`Intent` is *extraction only*. It records the constraints the user actually
stated and nothing else. Every field is optional, because a prompt that says
nothing about weather must produce a plan with no weather — not a guess.

`ScenePlan` is *completion*. Conditioned on the prompt and the extracted
constraints, it resolves what was ambiguous, fills in what downstream modules
need, and lands on P = (regions, terrain spec, object spec) plus the global
attributes both specs are read under.

Neither stage produces geometry. These models are handed straight to `@tool`
functions, so every docstring and `Field(description=...)` below is prompt text
the model reads, and validation failures come back to it as tool errors.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..llm import Strict

Placement = Literal[
    "center",
    "north",
    "south",
    "east",
    "west",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
    "edges",
    "scattered",
    "unspecified",
]

RelationType = Literal[
    "north_of",
    "south_of",
    "east_of",
    "west_of",
    "adjacent_to",
    "surrounds",
    "inside",
    "above",
    "below",
    "connects_to",
    "faces",
]

# -- stage 1a: intent analysis -------------------------------------------
# Extraction only. Absent means the user did not say it; do not fill it in.


class StatedRelation(Strict):
    """A spatial relationship the user stated between two things they named."""

    subject: str = Field(description="The thing being placed, as the user named it.")
    relation: RelationType = Field(description="How it relates to the target.")
    target: str = Field(description="The thing it is placed relative to.")
    verbatim: str = Field(
        description="The words from the prompt this came from, quoted exactly."
    )


class StatedEntity(Strict):
    """A region or object the user explicitly named.

    Record only attributes the prompt states. Leave the rest unset — the
    planning stage fills those in, and a guess here would be indistinguishable
    from a requirement.
    """

    name: str = Field(description="The thing, as the user named it.")
    kind: Literal["region", "object"] = Field(
        description="Whether the user described an area of the scene or a thing in it."
    )
    attributes: list[str] = Field(
        default_factory=list,
        description="Attributes the prompt states about it, e.g. \"burnt out\", "
        "\"three of them\". Stated only.",
    )
    verbatim: str = Field(
        description="The words from the prompt this came from, quoted exactly."
    )


class Intent(Strict):
    """The constraints the user explicitly expressed — nothing more.

    This is a summary of the prompt, not a plan. If the prompt does not say it,
    it does not belong here. Under-filling is correct; inventing is not.
    """

    prompt: str = Field(description="The user's prompt, verbatim.")
    scene_type: str | None = Field(
        default=None,
        description="The kind of scene, if the user said what it is. Otherwise null.",
    )
    theme: str | None = Field(
        default=None, description="The theme, if stated. Otherwise null."
    )
    visual_style: str | None = Field(
        default=None,
        description="Style, mood, light, or weather, if stated. Otherwise null.",
    )
    scale: str | None = Field(
        default=None,
        description="Any size the user gave, in their words, e.g. \"about a kilometre\". "
        "Otherwise null.",
    )
    entities: list[StatedEntity] = Field(
        default_factory=list,
        description="Regions and objects the user named. Empty if they named none.",
    )
    relations: list[StatedRelation] = Field(
        default_factory=list,
        description="Spatial relationships the user stated. Empty if they stated none.",
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="Other requirements or exclusions the user gave, e.g. \"no water\", "
        "\"keep it walkable\".",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Points the prompt leaves open that the planning stage must resolve. "
        "Name the gap; do not resolve it here.",
    )


# -- stage 1b: scene-level planning --------------------------------------
# Completion. Resolves ambiguity and fills what downstream modules need.


class Globals(Strict):
    """Scene-wide attributes that terrain and objects are both read under.

    Shared so that terrain generation and object generation work from one
    visual and semantic interpretation rather than two.
    """

    theme: str = Field(description='The scene\'s theme, e.g. "abandoned war zone".')
    visual_style: str = Field(
        description="How the scene should look overall, in two or three sentences. "
        "Cover the dominant colours here — this is the only style field, and terrain "
        "and objects are both generated under it."
    )
    material_preferences: list[str] = Field(
        default_factory=list,
        description='Materials the scene favours, e.g. ["weathered steel", "dry stone"].',
    )
    atmosphere: str = Field(
        description="Time of day, weather, and light in one phrase, e.g. "
        '"late afternoon, dust haze, low raking light".'
    )
    scale_m: float = Field(
        gt=0,
        le=2000,
        description="Width of the square scene, in metres. Keep under 1000 unless the "
        "prompt demands more.",
    )


class SpatialRelation(Strict):
    """How one region sits relative to another."""

    relation: RelationType = Field(description="The relationship.")
    target: str = Field(description="Id of the region this relates to.")


class Region(Strict):
    """One major region of the scene — an area, not geometry."""

    id: str = Field(description='Kebab-case identifier, e.g. "north-ridge".')
    description: str = Field(description="One or two sentences on what this region is.")
    role: str = Field(
        description='What it does for the scene, e.g. "focal landmark", "approach", '
        '"backdrop".'
    )
    placement: Placement = Field(description="Where it sits on the map.")
    extent: float = Field(
        gt=0, le=1, description="Roughly how much of the map it covers, 0 to 1."
    )
    relations: list[SpatialRelation] = Field(
        default_factory=list,
        description="How it sits relative to other regions. Reference regions by id.",
    )


class TerrainSpec(Strict):
    """The terrain one region is made of. One per region."""

    region_id: str = Field(description="Id of the region this describes.")
    terrain_type: str = Field(description='Kebab-case type, e.g. "dune-field", "hardpan".')
    landform: str = Field(
        description="The shape of the ground — what formed it, how much relief it "
        "has, and how it reads. One or two sentences."
    )
    surface: str = Field(description='Ground surface, e.g. "loose yellow sand".')
    assets: list[str] = Field(
        default_factory=list,
        description="Terrain-bound dressing this region needs, e.g. "
        '["sand ripples", "exposed bedrock"]. Not standalone objects.',
    )


class ObjectSpec(Strict):
    """One category of object placed in one region.

    Give an approximate `density_per_100m2`. Reach for `approx_count` only when
    the user asked for a specific number of something — that count is a stated
    constraint, and a density would quietly lose it. Exactly one of the two.

    How big each instance is and where exactly it sits are for downstream
    generation; say what it is, how it looks, and how much of it there is.
    """

    region_id: str = Field(description="Id of the region these belong to.")
    category: str = Field(description='What it is, e.g. "abandoned jeep", "acacia".')
    appearance: str = Field(
        description="How it should look, including its rough size, in one or two sentences."
    )
    density_per_100m2: float | None = Field(
        default=None,
        gt=0,
        description="Approximate instances per 100 m². The normal way to specify an "
        "object category.",
    )
    approx_count: int | None = Field(
        default=None,
        ge=1,
        description="Exact number, for a category the user asked for by count — "
        '"a burnt-out jeep" is one jeep, not a density. Use only when the count comes '
        "from the prompt.",
    )
    relations: list[SpatialRelation] = Field(
        default_factory=list,
        description="Region-level relationships, e.g. clustered along a region border.",
    )

    @model_validator(mode="after")
    def _check_placement(self) -> ObjectSpec:
        if (self.density_per_100m2 is None) == (self.approx_count is None):
            raise ValueError(
                f"{self.category!r}: set exactly one of density_per_100m2 or "
                "approx_count, not both or neither"
            )
        return self


class ScenePlan(Strict):
    """P = (regions, terrain spec, object spec) plus the globals they share.

    The finished stage-1 artifact: a structured scene specification, not
    terrain geometry or object instances.
    """

    name: str = Field(description='A short name for the scene, e.g. "Desert Battlefield".')
    scene_type: str = Field(
        description='What kind of scene this is, e.g. "desert battlefield".'
    )
    globals: Globals
    regions: list[Region] = Field(min_length=1)
    terrain: list[TerrainSpec] = Field(min_length=1)
    objects: list[ObjectSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_references(self) -> ScenePlan:
        ids = [r.id for r in self.regions]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate region ids: {sorted(dupes)}")
        known = set(ids)

        covered = [t.region_id for t in self.terrain]
        if unknown := sorted(set(covered) - known):
            raise ValueError(f"terrain references unknown regions: {unknown}")
        if missing := sorted(known - set(covered)):
            raise ValueError(f"regions with no terrain spec: {missing}")
        if extra := sorted({r for r in covered if covered.count(r) > 1}):
            raise ValueError(f"more than one terrain spec for regions: {extra}")

        if unknown := sorted({o.region_id for o in self.objects} - known):
            raise ValueError(f"objects reference unknown regions: {unknown}")

        targets = {rel.target for r in self.regions for rel in r.relations}
        targets |= {rel.target for o in self.objects for rel in o.relations}
        if unknown := sorted(targets - known):
            raise ValueError(f"relations reference unknown regions: {unknown}")

        total = sum(r.extent for r in self.regions)
        if not 0.7 <= total <= 1.5:
            raise ValueError(
                f"region extents should cover about the whole map; they sum to {total:.2f}"
            )
        return self


__all__ = [
    "Intent",
    "StatedEntity",
    "StatedRelation",
    "ScenePlan",
    "Globals",
    "Region",
    "SpatialRelation",
    "TerrainSpec",
    "ObjectSpec",
    "Placement",
    "RelationType",
]
