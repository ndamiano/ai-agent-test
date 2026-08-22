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

import base64
import json
from pathlib import Path

from ..llm import LLMHarness, Message
from ..planning.models import ScenePlan
from ..terrain.models import TerrainPlan
from .models import RegionalPlan
from .tools import RegionalPlanBuilder

DEFAULT_MODEL = "qwen3.8_27b"

_HERE = Path(__file__).parent
REGIONAL_PROMPT = (_HERE / "regional_prompt.txt").read_text().strip()

# The top-down view says where the regions actually ended up; the obliques say
# what they look like standing in them. Both questions matter to this stage, and
# more views than this costs context without adding evidence.
DEFAULT_VIEWS = ("view_top.png", "view_0.png", "view_2.png")


def _image_part(path: Path) -> dict:
    """One render, as the content part an OpenAI-compatible endpoint expects."""
    data = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


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
    return (
        f"Scene plan for {scene.name!r}:\n"
        f"{json.dumps(scene.model_dump(exclude_none=True), indent=1)}\n\n"
        f"Terrain specification that was built from it:\n"
        f"{json.dumps(terrain.model_dump(exclude_none=True), indent=1)}\n\n"
        f"Renders of the world as it now stands: a top-down view of the whole "
        f"{terrain.world.size_m:g} m map, then oblique views from two sides.\n\n"
        f"Decide which regions to populate with objects."
    )


def plan_regions(
    scene: ScenePlan,
    terrain: TerrainPlan,
    views: list[Path] | None = None,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.5,
) -> RegionalPlan:
    """Turn the scene plan and the built terrain into a regional plan.

    Cooler than terrain planning. This stage is mostly judgement about what is
    already there, and the numbers it does invent — sizes and counts — are ones
    the rest of the pipeline takes literally.
    """
    builder = RegionalPlanBuilder(scene, terrain)
    harness = LLMHarness(
        model=model,
        tools=builder.tools,
        system=REGIONAL_PROMPT,
        temperature=temperature,
    )
    message = Message("user", [
        {"type": "text", "text": _briefing(scene, terrain)},
        *[_image_part(view) for view in (views or [])],
    ])
    harness.send_message_with_tools([message])
    return builder.finish()


def regional_stage(
    scene: ScenePlan,
    terrain: TerrainPlan,
    out_dir: Path | str,
    *,
    model: str = DEFAULT_MODEL,
) -> RegionalPlan:
    """Plan the regions and write the regional plan to `out_dir`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    regional = plan_regions(scene, terrain, views_in(out_dir), model=model)
    (out_dir / "regional_plan.json").write_text(regional.model_dump_json(indent=2))
    return regional


__all__ = [
    "plan_regions",
    "regional_stage",
    "views_in",
    "REGIONAL_PROMPT",
    "DEFAULT_MODEL",
]
