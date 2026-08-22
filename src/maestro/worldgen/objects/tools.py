"""The tools the regional planning agent calls.

    builder = RegionalPlanBuilder(scene, terrain)
    harness = LLMHarness(..., tools=builder.tools)
    harness.send_message_with_tools(prompt)
    regional = builder.finish()

Every region in the scene plan must be either developed or skipped before this
will finish. Selecting a subset is the point of the stage, but a region that
falls out of the selection because the agent forgot about it is not a
selection, and the difference is invisible in the result unless the stage
insists on it.
"""
from __future__ import annotations

from ..llm import tool

from ..planning.models import ScenePlan
from ..terrain.models import TerrainPlan
from .models import RegionalPlan, RegionalSpec


class RegionalPlanBuilder:
    """Accumulates the regional plan against the scene plan and the terrain it got."""

    def __init__(self, scene: ScenePlan, terrain: TerrainPlan) -> None:
        self.scene = scene
        self.terrain = terrain
        self.specs: list[RegionalSpec] = []
        self.skipped: dict[str, str] = {}
        self._known = {r.id for r in scene.regions}

    @property
    def tools(self) -> list:
        """The tool list to hand to `LLMHarness(tools=...)`."""

        @tool
        def develop_region(spec: RegionalSpec) -> str:
            """Select one region for development and specify it fully.

            Call once per region you are developing. The whole specification
            goes in one call — the function, the object categories, the spatial
            rules and the appearance are decided together or not at all.

            Sizes are metres and counts are per region. A category whose
            instances are under about a metre across cannot be reconstructed
            individually from a region view; say so with a density and let it be
            scatter, or make it bigger, but do not ask for forty of them.
            """
            if spec.region_id not in self._known:
                return (
                    f"error: no region {spec.region_id!r} in the scene plan; "
                    f"regions are {sorted(self._known)}"
                )
            if any(s.region_id == spec.region_id for s in self.specs):
                return f"error: {spec.region_id!r} is already developed"
            if spec.region_id in self.skipped:
                return (
                    f"error: {spec.region_id!r} was skipped; it cannot also be "
                    f"developed"
                )
            self.specs.append(spec)
            total = sum(
                o.approx_count for o in spec.objects if o.approx_count is not None
            )
            return (
                f"developing {spec.region_id!r}: {len(spec.objects)} categories, "
                f"{total} placed by count, {len(spec.spatial)} spatial rules. "
                f"{self._remaining()}"
            )

        @tool
        def skip_region(region_id: str, reason: str) -> str:
            """Record that a region is deliberately left as terrain.

            A region the terrain already delivers — a backdrop ridge, an empty
            approach — belongs here. This is not a failure state; it is how the
            selection ends up smaller than the full set of regions.

            Args:
                region_id: Id of the region, from the scene plan.
                reason: Why it needs nothing, in one sentence.
            """
            if region_id not in self._known:
                return (
                    f"error: no region {region_id!r} in the scene plan; "
                    f"regions are {sorted(self._known)}"
                )
            if any(s.region_id == region_id for s in self.specs):
                return f"error: {region_id!r} is already developed"
            self.skipped[region_id] = reason
            return f"skipping {region_id!r}. {self._remaining()}"

        @tool
        def finish_regional_plan() -> str:
            """Call when every region has been developed or skipped.

            Reports what is still undecided, or what is inconsistent, so it can
            be fixed and called again. Stop only once this says it is complete.
            """
            if undecided := self._undecided():
                return (
                    f"not finished: no decision yet on {sorted(undecided)}. "
                    f"Develop each one or skip it."
                )
            try:
                plan = self.finish()
            except Exception as exc:  # ValueError or pydantic ValidationError
                return f"plan not valid yet, fix and call again:\n{exc}"
            return (
                f"regional plan complete: {len(plan.regions)} regions developed, "
                f"{len(self.skipped)} left as terrain. Nothing further to do."
            )

        return [develop_region, skip_region, finish_regional_plan]

    def _undecided(self) -> set[str]:
        return self._known - {s.region_id for s in self.specs} - set(self.skipped)

    def _remaining(self) -> str:
        undecided = self._undecided()
        return (
            f"Still undecided: {sorted(undecided)}"
            if undecided
            else "Every region now has a decision."
        )

    def finish(self) -> RegionalPlan:
        """Validate what was recorded and return the regional plan.

        Raises `ValueError` if nothing was selected — a run that develops no
        region at all has not planned anything, whatever its reasons were.
        """
        if not self.specs:
            raise ValueError(
                "no region was selected for development; P_regional cannot be empty"
            )
        return RegionalPlan(regions=self.specs, skipped=dict(self.skipped))


__all__ = ["RegionalPlanBuilder"]
