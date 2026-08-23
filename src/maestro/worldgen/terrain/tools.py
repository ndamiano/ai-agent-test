"""The tools the terrain planning agent calls.

    builder = TerrainPlanBuilder(scene_plan)
    harness = LLMHarness(..., tools=builder.tools)
    harness.send_message_with_tools(...)
    plan = builder.finish()

The agent fills P_terrain one component at a time, and each tool checks what it
can at the moment of the call rather than leaving everything to `finish()`.
An id that names no region, a second material for a region that already has
one, coverage that has drifted past one — all of these come back as tool results
the model can still act on, while a failure raised from `finish()` arrives after
the conversation is over.

State lives on the builder, so two runs in one process never see each other.
"""
from __future__ import annotations

from ..llm import tool

from ..planning.models import ScenePlan
from .models import (
    RegionLayout,
    RegionMaterial,
    RegionTerrain,
    TerrainAsset,
    TerrainPlan,
    WorldParams,
)


class TerrainPlanBuilder:
    """Accumulates P_terrain against the scene plan it must be consistent with."""

    def __init__(self, scene: ScenePlan) -> None:
        self.scene = scene
        self.known = {r.id for r in scene.regions}
        self.world: WorldParams | None = None
        self.layout: list[RegionLayout] = []
        self.terrain: list[RegionTerrain] = []
        self.materials: list[RegionMaterial] = []
        self.assets: list[TerrainAsset] = []
        self.concept: str | None = None

    @property
    def tools(self) -> list:
        """The tool list to hand to `LLMHarness(tools=...)`."""

        @tool
        def set_world(world: WorldParams) -> str:
            """Set the world-level numbers. Call this once, first.

            Everything else is measured against these, so a region placed before
            the world has a size has nothing to be sized against.

            Args:
                world: Size, sea level, boundary blend width, height-field grid.
            """
            self.world = world
            water = (
                "no standing water"
                if world.sea_level_m < -100
                else f"sea at {world.sea_level_m:g} m"
            )
            return (
                f"world set: {world.size_m:g} m square, {water}, boundaries blend "
                f"over {world.boundary_blend_m:g} m, {world.heightmap_resolution} grid"
            )

        @tool
        def place_region(layout: RegionLayout) -> str:
            """Place one region on the map: category, centre, radius, coverage.

            Call once per region in the scene plan. Place every one of them —
            a region with no place on the map cannot be built, textured or
            scattered into.

            Work from the scene plan's stated placements and relations. Two
            regions that are said to border each other should have discs that
            touch; a region said to surround another should be centred on it
            with a larger radius.
            """
            if layout.region_id not in self.known:
                return (
                    f"error: no region {layout.region_id!r} in the scene plan; "
                    f"regions are {sorted(self.known)}"
                )
            if any(r.region_id == layout.region_id for r in self.layout):
                return f"error: {layout.region_id!r} is already placed"
            self.layout.append(layout)
            total = sum(r.coverage for r in self.layout)
            unplaced = sorted(self.known - {r.region_id for r in self.layout})
            return (
                f"placed {layout.region_id!r} as {layout.category} at "
                f"{layout.center} r={layout.radius:g}; coverage now {total:.2f}; "
                f"still to place: {unplaced or ['<none>']}"
            )

        @tool
        def set_region_terrain(terrain: RegionTerrain) -> str:
            """Set one region's elevation, noise bands and landform operators.

            Call once per region. This is theta_terrain for that region: the
            height it sits at, the noise that roughens it, and the operators that
            give it its landform.

            Height differences between regions come from base_elevation_m. An
            operator makes a shape within a region; it does not lift the region
            relative to its neighbours.
            """
            if terrain.region_id not in self.known:
                return f"error: no region {terrain.region_id!r} in the scene plan"
            if any(r.region_id == terrain.region_id for r in self.terrain):
                return f"error: {terrain.region_id!r} already has terrain parameters"
            self.terrain.append(terrain)
            relief = sum(o.relief_m for o in terrain.operators)
            amplitude = sum(b.amplitude_m for b in terrain.noise)
            waiting = sorted(self.known - {r.region_id for r in self.terrain})
            return (
                f"{terrain.region_id!r}: base {terrain.base_elevation_m:g} m, "
                f"{len(terrain.noise)} noise bands ({amplitude:g} m total), "
                f"{len(terrain.operators)} operators ({relief:g} m relief); "
                f"still missing terrain: {waiting or ['<none>']}"
            )

        @tool
        def set_region_material(material: RegionMaterial) -> str:
            """Set one region's surface: what it is, its worn variant, how big it repeats.

            Call once per region. A region has two grounds, not one: the base
            surface and the variant it wears through to. The renderer blends
            them by slope and by patches, so a region given the same thing twice
            is a region with one ground.

            `scale_m` is the number the rest of the pipeline cannot recover on
            its own — how wide one repeat of this surface is in metres.
            """
            if material.region_id not in self.known:
                return f"error: no region {material.region_id!r} in the scene plan"
            if any(m.region_id == material.region_id for m in self.materials):
                return f"error: {material.region_id!r} already has a material"
            self.materials.append(material)
            waiting = sorted(self.known - {m.region_id for m in self.materials})
            return (
                f"{material.region_id!r}: {material.surface!r} worn to "
                f"{material.variant!r}, "
                f"repeating every {material.scale_m:g} m; "
                f"still missing materials: {waiting or ['<none>']}"
            )

        @tool
        def add_terrain_asset(asset: TerrainAsset) -> str:
            """Add one category of terrain-bound object to scatter.

            Rocks, vegetation clumps, driftwood, bones, scree — things that
            follow the ground and repeat. Anything with a function, an identity,
            or a relationship to something else belongs to the later regional
            stage and must not be added here.

            One entry covers every instance of that category across every region
            it appears in.

            `detail` decides how many triangles the mesh is built with, so say
            what the shape actually is: a branching tree is 'intricate', a
            boulder is 'simple'. Every instance pays that cost, and a world
            scatters these in the thousands.
            """
            if unknown := sorted(set(asset.regions) - self.known):
                return (
                    f"error: {asset.category!r} scatters in unknown regions "
                    f"{unknown}; regions are {sorted(self.known)}"
                )
            if any(a.category == asset.category for a in self.assets):
                return f"error: {asset.category!r} has already been added"
            self.assets.append(asset)
            return (
                f"added {asset.category!r} at {asset.per_hectare:g}/ha in "
                f"{asset.regions}, {asset.detail} "
                f"({len(self.assets)} asset categories)"
            )

        @tool
        def set_concept(concept: str) -> str:
            """Describe the whole world as one aerial photograph of it.

            This is rendered as the concept image and shown to everything drawn
            afterwards, so it has to be about what a camera would see: landforms,
            where they sit, colours, ground cover, light. Not the story, not any
            single object, no camera or lens talk.

            Args:
                concept: One paragraph, three or four sentences.
            """
            self.concept = concept.strip()
            return f"concept set ({len(self.concept.split())} words)"

        @tool
        def review_plan() -> str:
            """Check what is still missing or inconsistent before finishing.

            Call this when you think you are done. It reports gaps rather than
            fixing them.
            """
            return self._review()

        return [
            set_world,
            place_region,
            set_region_terrain,
            set_region_material,
            add_terrain_asset,
            set_concept,
            review_plan,
        ]

    # -- assembly ------------------------------------------------------------

    def _review(self) -> str:
        problems = []
        if self.world is None:
            problems.append("no world parameters: call set_world")
        if not self.concept:
            problems.append("no concept paragraph: call set_concept")
        for label, have in (
            ("placed", {r.region_id for r in self.layout}),
            ("terrain", {r.region_id for r in self.terrain}),
            ("materials", {m.region_id for m in self.materials}),
        ):
            if missing := sorted(self.known - have):
                problems.append(f"regions with no {label} entry: {missing}")
        if not self.assets:
            problems.append(
                "no terrain assets: a world with nothing scattered on it reads as bare"
            )
        if self.layout:
            total = sum(r.coverage for r in self.layout)
            if not 0.7 <= total <= 1.4:
                problems.append(
                    f"coverage sums to {total:.2f}; it should account for about the "
                    f"whole world"
                )
        if self.world and self.terrain:
            lowest = min(r.base_elevation_m for r in self.terrain)
            if self.world.sea_level_m > lowest:
                problems.append(
                    f"sea level {self.world.sea_level_m:g} m is above the lowest region "
                    f"base {lowest:g} m — intended only if this world has water"
                )
        return "; ".join(problems) if problems else "plan is complete and consistent"

    def finish(self) -> TerrainPlan:
        """Validate everything recorded and return P_terrain.

        Unlike stage 1's intent, an under-filled terrain plan is not a valid
        answer: every region needs a place, parameters and a surface, or the
        stages after this have nothing to build from.
        """
        if self.world is None:
            raise ValueError("terrain planning finished without calling set_world")
        if not self.concept:
            raise ValueError("terrain planning finished without a concept paragraph")
        return TerrainPlan(
            world=self.world,
            layout=self.layout,
            terrain=self.terrain,
            materials=self.materials,
            assets=self.assets,
            concept=self.concept,
        )


__all__ = ["TerrainPlanBuilder"]
