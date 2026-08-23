"""Run the regional planning agent.

    regional = plan_regions(scene, terrain, views)

The agent is given both the scene specification as text and the world as
pictures: the terrain plan says what was asked for, and the renders say what
arrived. They disagree often enough that giving only one of them would be a
mistake. The terrain refinement loop already established that this model reads
renders and acts on what it sees, so the same views serve here.

The output is a selection and a specification, not geometry. Nothing is
generated at this stage and nothing touches the image model.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..llm import LLMHarness, Message, image_part
from ..planning.models import ScenePlan
from ..terrain.models import TerrainPlan
from .models import RegionalPlan
from .tools import RegionalPlanBuilder

_HERE = Path(__file__).parent
REGIONAL_PROMPT = (_HERE / "regional_prompt.txt").read_text().strip()
BRIEFING = (_HERE / "regional_briefing.txt").read_text().strip()

# The top-down view says where the regions actually ended up; the obliques say
# what they look like standing in them. Both questions matter to this stage, and
# more views than this costs context without adding evidence.
DEFAULT_VIEWS = ("view_top.png", "view_0.png", "view_2.png")


def views_in(out_dir: Path | str, names: tuple[str, ...] = DEFAULT_VIEWS) -> list[Path]:
    """The renders to show the planner, skipping any that were not written."""
    out_dir = Path(out_dir)
    return [p for p in (out_dir / name for name in names) if p.exists()]


def _briefing(scene: ScenePlan, terrain: TerrainPlan) -> str:
    """What the planner is told, alongside the renders.

    The whole scene plan and the whole terrain plan. The question this stage
    answers — which regions are underserved — cannot be answered from a summary
    of either, because the gap between them is exactly what a summary loses.
    """
    return BRIEFING.format(
        name=scene.name,
        scene=json.dumps(scene.model_dump(exclude_none=True), indent=1),
        terrain=json.dumps(terrain.model_dump(exclude_none=True), indent=1),
        size_m=terrain.world.size_m,
    )


def plan_regions(
    scene: ScenePlan,
    terrain: TerrainPlan,
    views: list[Path] | None = None,
    *,
    temperature: float = 0.5,
) -> RegionalPlan:
    """Turn the scene plan and the built terrain into a regional plan.

    Cooler than terrain planning. This stage is mostly judgement about what is
    already there, and the numbers it does invent — sizes and counts — are ones
    the rest of the pipeline takes literally.
    """
    builder = RegionalPlanBuilder(scene, terrain)
    harness = LLMHarness(
        tools=builder.tools,
        system=REGIONAL_PROMPT,
        temperature=temperature,
    )
    message = Message("user", [
        {"type": "text", "text": _briefing(scene, terrain)},
        *[image_part(view) for view in (views or [])],
    ])
    harness.send_message_with_tools([message])
    return builder.finish()


def regional_stage(
    scene: ScenePlan,
    terrain: TerrainPlan,
    out_dir: Path | str,
) -> RegionalPlan:
    """Plan the regions and write the regional plan to `out_dir`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    regional = plan_regions(scene, terrain, views_in(out_dir))
    (out_dir / "regional_plan.json").write_text(regional.model_dump_json(indent=2))
    return regional


__all__ = [
    "plan_regions",
    "regional_stage",
    "views_in",
    "REGIONAL_PROMPT",
]
