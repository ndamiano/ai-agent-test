"""What the regional planning agent decides for each region it develops.

The scene plan already says what regions exist and, roughly, what should be in
them. What it does not say is which regions are worth developing, or anything an
image generator and a segmenter can actually act on. This stage completes only
that: it picks which regions to develop and turns the scene-level requirements
into per-region ones.

Two fields here earn their place from what the pipeline downstream can and
cannot do:

  * `typical_size_m` on every category. Everything after this is monocular —
    an object's size in the composition image is the only evidence of its size
    in the world, and a segmenter that works down to about a tenth of the frame
    is what decides whether a category is reconstructible at all. A planner that
    says "boulder" without saying whether it means a kerbstone or a house has
    not specified anything.
  * `reason` on the region. The selection is a subset, and a run that quietly
    drops half the world should say why it did.

Sizes are metres, and counts are per region, not per world.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    """Reject unknown keys instead of silently dropping them."""

    model_config = ConfigDict(extra="forbid")


class RegionalObject(Strict):
    """One category to introduce into one region.

    Give either `approx_count` or `density_per_100m2`, never both. A count is
    what you want for anything the region is built around, where the number is
    part of the design; a density is what you want for anything that fills the
    region out, where the number is a consequence of how big the region is.
    """

    category: str = Field(
        description='What it is, in the words an image and a segmenter both '
        'understand, e.g. "canvas army tent", "burnt-out jeep". Singular.'
    )
    appearance: str = Field(
        description="How this one looks — form, material, condition, colour. One "
        "or two sentences. This goes into the composition prompt."
    )
    typical_size_m: float = Field(
        gt=0.05, le=200.0,
        description="Longest horizontal dimension of one instance, in metres. "
        "This is what decides how the region is framed and whether the category "
        "can be reconstructed at all, so give the real size, not an impression.",
    )
    approx_count: int | None = Field(
        default=None, gt=0, le=200,
        description="How many, when the number is part of the design.",
    )
    density_per_100m2: float | None = Field(
        default=None, gt=0,
        description="Instances per 100 m², when the number is a consequence of "
        "the region's size rather than a decision.",
    )

    @model_validator(mode="after")
    def _one_amount(self) -> RegionalObject:
        given = (self.approx_count is not None) + (self.density_per_100m2 is not None)
        if given != 1:
            raise ValueError(
                f"{self.category!r}: give exactly one of approx_count or "
                f"density_per_100m2, not {given}"
            )
        return self


class SpatialRule(Strict):
    """One relationship the region's objects have to hold.

    Object-object and object-terrain both, which is why `target` is free text: a
    rule may point at another category or at the ground itself.
    """

    subject: str = Field(description="Category the rule is about.")
    relation: str = Field(
        description='The relationship, in plain words, e.g. "clustered around", '
        '"facing", "scattered downslope of", "half-buried in".'
    )
    target: str = Field(
        description='What it relates to — another category, or a terrain feature '
        'such as "the ridge crest" or "the track".'
    )


class RegionalSpec(Strict):
    """Everything one selected region needs, and nothing it can infer."""

    region_id: str = Field(description="Id of the region, from the scene plan.")
    reason: str = Field(
        description="Why this region is worth developing: what the scene plan "
        "asks for here that the terrain has not already delivered."
    )
    function: str = Field(
        description="phi_r — what this region is FOR, in one sentence, e.g. "
        '"a forward camp the approach road runs into".'
    )
    objects: list[RegionalObject] = Field(
        min_length=1,
        description="C_r^object — the categories to introduce here.",
    )
    spatial: list[SpatialRule] = Field(
        default_factory=list,
        description="p_r^spatial — how the objects sit relative to each other "
        "and to the terrain. Empty means the arrangement is unconstrained, "
        "which is rarely what you mean.",
    )
    appearance: str = Field(
        description="p_r^appearance — the region's look and style in two or "
        "three sentences: palette, condition, light. Read alongside the scene "
        "globals, not instead of them."
    )

    @model_validator(mode="after")
    def _distinct_categories(self) -> RegionalSpec:
        names = [o.category for o in self.objects]
        if dupes := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"{self.region_id}: repeated categories: {dupes}")
        known = set(names)
        for rule in self.spatial:
            if rule.subject not in known:
                raise ValueError(
                    f"{self.region_id}: spatial rule about {rule.subject!r}, which "
                    f"is not one of this region's categories: {sorted(known)}"
                )
        return self


class RegionalPlan(Strict):
    """The selected regions and their specifications.

    A subset, deliberately. Developing every region is the thing this stage
    exists to avoid.
    """

    regions: list[RegionalSpec] = Field(min_length=1)
    skipped: dict[str, str] = Field(
        default_factory=dict,
        description="Regions deliberately left as terrain, and why. Kept because "
        "R+ being smaller than R is a decision, and a run that cannot say which "
        "regions it declined to develop cannot be reviewed.",
    )

    @model_validator(mode="after")
    def _unique(self) -> RegionalPlan:
        ids = [r.region_id for r in self.regions]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"more than one spec for: {dupes}")
        if both := sorted(set(ids) & set(self.skipped)):
            raise ValueError(f"developed and skipped at once: {both}")
        return self

    def for_region(self, region_id: str) -> RegionalSpec:
        for spec in self.regions:
            if spec.region_id == region_id:
                return spec
        raise KeyError(f"no regional spec for {region_id!r}")


__all__ = ["RegionalObject", "SpatialRule", "RegionalSpec", "RegionalPlan", "Strict"]
