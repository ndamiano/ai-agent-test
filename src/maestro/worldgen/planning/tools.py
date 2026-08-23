"""The tools each stage-1 agent calls.

    intent_builder = IntentBuilder()
    harness = LLMHarness(..., tools=intent_builder.tools)
    harness.send_message_with_tools(prompt)
    intent = intent_builder.finish()

    plan_builder = PlanBuilder()
    ...
    plan = plan_builder.finish()

State lives on the builders, not in module globals, so two runs in one process
never see each other's work.
"""
from __future__ import annotations

from ..llm import tool

from .models import (
    Globals,
    Intent,
    ObjectSpec,
    Region,
    ScenePlan,
    StatedEntity,
    StatedRelation,
    TerrainSpec,
)


class IntentBuilder:
    """Accumulates an `Intent` — the constraints the user actually stated."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        self.scene_type: str | None = None
        self.theme: str | None = None
        self.visual_style: str | None = None
        self.scale: str | None = None
        self.entities: list[StatedEntity] = []
        self.relations: list[StatedRelation] = []
        self.preferences: list[str] = []
        self.ambiguities: list[str] = []
        self._done = False

    @property
    def tools(self) -> list:
        """The tool list to hand to `LLMHarness(tools=...)`."""

        @tool
        def set_stated_basics(
            scene_type: str | None = None,
            theme: str | None = None,
            visual_style: str | None = None,
            scale: str | None = None,
        ) -> str:
            """Record the scene-wide constraints the user stated. Call this once, first.

            Leave an argument out when the prompt does not state it. A guess here
            is worse than a gap — the planning stage cannot tell the two apart.
            Calling again only fills in what you pass; it never clears what is set.

            Args:
                scene_type: The kind of scene, if the user said what it is.
                theme: The theme, if stated.
                visual_style: Style, mood, light, or weather, if stated.
                scale: Any size the user gave, in their own words.
            """
            for attr, value in (
                ("scene_type", scene_type),
                ("theme", theme),
                ("visual_style", visual_style),
                ("scale", scale),
            ):
                if value is not None:
                    setattr(self, attr, value)
            stated = [
                k
                for k, v in (
                    ("scene_type", scene_type),
                    ("theme", theme),
                    ("visual_style", visual_style),
                    ("scale", scale),
                )
                if v is not None
            ]
            return f"basics recorded; stated: {stated or ['<none>']}"

        @tool
        def add_stated_entity(entity: StatedEntity) -> str:
            """Record one region or object the user explicitly named.

            Only what the prompt says. Its unstated attributes are not yours to
            fill in.
            """
            self.entities.append(entity)
            return f"recorded {entity.kind} {entity.name!r} ({len(self.entities)} entities)"

        @tool
        def add_stated_relation(relation: StatedRelation) -> str:
            """Record one spatial relationship the user stated between things they named."""
            self.relations.append(relation)
            return (
                f"recorded {relation.subject!r} {relation.relation} {relation.target!r} "
                f"({len(self.relations)} relations)"
            )

        @tool
        def add_preference(preference: str) -> str:
            """Record one other requirement or exclusion the user gave.

            Args:
                preference: The requirement, e.g. "no water", "keep it walkable".
            """
            self.preferences.append(preference)
            return f"recorded preference ({len(self.preferences)} so far)"

        @tool
        def add_ambiguity(ambiguity: str) -> str:
            """Note one thing the prompt leaves open for the planning stage.

            Name the gap; do not resolve it.

            Args:
                ambiguity: The open point, e.g. "no time of day given".
            """
            self.ambiguities.append(ambiguity)
            return f"noted ambiguity ({len(self.ambiguities)} so far)"

        @tool
        def finish_intent() -> str:
            """Call when every stated constraint has been recorded."""
            self._done = True
            return (
                f"intent complete: {len(self.entities)} entities, "
                f"{len(self.relations)} relations, {len(self.preferences)} preferences, "
                f"{len(self.ambiguities)} ambiguities"
            )

        return [
            set_stated_basics,
            add_stated_entity,
            add_stated_relation,
            add_preference,
            add_ambiguity,
            finish_intent,
        ]

    def finish(self) -> Intent:
        """Validate what was recorded and return the intent.

        A prompt that states almost nothing yields an almost-empty `Intent`.
        That is the correct result, so nothing here is required.
        """
        return Intent(
            prompt=self.prompt,
            scene_type=self.scene_type,
            theme=self.theme,
            visual_style=self.visual_style,
            scale=self.scale,
            entities=self.entities,
            relations=self.relations,
            preferences=self.preferences,
            ambiguities=self.ambiguities,
        )


class PlanBuilder:
    """Accumulates a `ScenePlan` — P = (regions, terrain, objects) plus globals."""

    def __init__(self) -> None:
        self.name: str | None = None
        self.scene_type: str | None = None
        self.globals: Globals | None = None
        self.regions: list[Region] = []
        self.terrain: list[TerrainSpec] = []
        self.objects: list[ObjectSpec] = []

    @property
    def tools(self) -> list:
        """The tool list to hand to `LLMHarness(tools=...)`."""

        @tool
        def set_globals(name: str, scene_type: str, globals: Globals) -> str:
            """Name the scene and set the attributes terrain and objects share.

            Call this once, first. Everything downstream is read under these, so
            they are what keeps terrain and objects visually consistent.

            Args:
                name: A short name for the scene, e.g. "Desert Battlefield".
                scene_type: What kind of scene this is.
                globals: Theme, visual style, palette, materials, atmosphere, scale.
            """
            self.name = name
            self.scene_type = scene_type
            self.globals = globals
            return (
                f"scene {name!r} set: {scene_type}, {globals.scale_m:g} m square, "
                f"{globals.atmosphere!r}"
            )

        @tool
        def add_region(region: Region) -> str:
            """Add one major region. Call once per region, before its terrain and objects.

            Regions may relate to each other by id in any order — a relation may
            name a region you have not added yet, so long as you add it before
            you finish.
            """
            if any(r.id == region.id for r in self.regions):
                return f"error: region {region.id!r} already exists"
            self.regions.append(region)
            known = {r.id for r in self.regions}
            pending = sorted({rel.target for rel in region.relations} - known)
            total = sum(r.extent for r in self.regions)
            note = f"; relations name regions not added yet: {pending}" if pending else ""
            return (
                f"added region {region.id!r} "
                f"({len(self.regions)} regions, extents sum to {total:.2f}){note}"
            )

        @tool
        def set_region_terrain(terrain: TerrainSpec) -> str:
            """Set the terrain for one region. Call once per region — every region needs one."""
            if not any(r.id == terrain.region_id for r in self.regions):
                known = [r.id for r in self.regions] or ["<none yet>"]
                return f"error: no region {terrain.region_id!r}; existing regions: {known}"
            if any(t.region_id == terrain.region_id for t in self.terrain):
                return f"error: region {terrain.region_id!r} already has a terrain spec"
            self.terrain.append(terrain)
            waiting = [r.id for r in self.regions if r.id not in {t.region_id for t in self.terrain}]
            return (
                f"terrain {terrain.terrain_type!r} set for {terrain.region_id!r}; "
                f"still missing terrain: {waiting or ['<none>']}"
            )

        @tool
        def add_region_object(obj: ObjectSpec) -> str:
            """Add one category of object to a region. Call once per category per region.

            Cover the range: what the region is built around, what fills it out,
            and the background scatter.
            """
            if not any(r.id == obj.region_id for r in self.regions):
                known = [r.id for r in self.regions] or ["<none yet>"]
                return f"error: no region {obj.region_id!r}; existing regions: {known}"
            if any(
                o.region_id == obj.region_id and o.category == obj.category
                for o in self.objects
            ):
                return f"error: {obj.category!r} already added to {obj.region_id!r}"
            self.objects.append(obj)
            amount = (
                f"~{obj.approx_count}"
                if obj.approx_count is not None
                else f"{obj.density_per_100m2:g}/100m²"
            )
            return (
                f"added {obj.category!r} to {obj.region_id!r} ({amount}) "
                f"— {len(self.objects)} object specs so far"
            )

        @tool
        def finish_plan() -> str:
            """Call when the plan is complete. Validates the whole plan.

            If anything is inconsistent — a region with no terrain, extents that
            do not cover the map, a relation naming a region that was never
            added — this reports it so you can fix it and call again. Only stop
            once this reports the plan is complete.
            """
            try:
                plan = self.finish()
            except Exception as exc:  # ValueError or pydantic ValidationError
                return f"plan not valid yet, fix and call again:\n{exc}"
            return (
                f"plan complete: {len(plan.regions)} regions, "
                f"{len(plan.objects)} object specs. Nothing further to do."
            )

        return [
            set_globals,
            add_region,
            set_region_terrain,
            add_region_object,
            finish_plan,
        ]

    def finish(self) -> ScenePlan:
        """Validate everything collected so far and return the plan.

        Raises `ValueError` if a required tool was never called,
        `ValidationError` if the assembled whole is inconsistent.
        """
        missing = []
        if self.globals is None:
            missing.append("set_globals")
        if not self.regions:
            missing.append("add_region")
        if not self.terrain:
            missing.append("set_region_terrain")
        if not self.objects:
            missing.append("add_region_object")
        if missing:
            raise ValueError(f"never called: {', '.join(missing)}")
        return ScenePlan(
            name=self.name,
            scene_type=self.scene_type,
            globals=self.globals,
            regions=self.regions,
            terrain=self.terrain,
            objects=self.objects,
        )


__all__ = ["IntentBuilder", "PlanBuilder"]
