"""Run stage 2a: the terrain planning agent.

    plan = plan_terrain(scene)                 # P_terrain
    concept = render_concept(plan, "concept.png")   # I_concept

Two halves, kept apart on purpose. The plan is the expensive part and it is
text; the concept image is a separate call on the image queue, so a completed
plan is written to disk before it is asked for and losing that call costs
nothing already done. `render_concept` can be run later, or not at all.

Nothing here reaches the network: the concept image carries the whole
reference load alone.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..backends import ImageModel
from ..llm import LLMHarness
from ..planning.models import ScenePlan
from .models import TerrainPlan
from .tools import TerrainPlanBuilder

DEFAULT_MODEL = "qwen3.8_27b"

_HERE = Path(__file__).parent
TERRAIN_PROMPT = (_HERE / "terrain_prompt.txt").read_text().strip()

CONCEPT_NEGATIVE = (
    "text, watermark, ui, map, diagram, people, vehicles, buildings, blurry, "
    "low detail, split screen, collage, illustration"
)


def plan_terrain(
    scene: ScenePlan,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.6,
) -> TerrainPlan:
    """Turn a scene plan into P_terrain.

    Runs warmer than intent analysis and cooler than scene planning: this stage
    invents numbers, but they have to agree with each other.
    """
    builder = TerrainPlanBuilder(scene)
    harness = LLMHarness(
        model=model,
        tools=builder.tools,
        system=TERRAIN_PROMPT,
        temperature=temperature,
    )
    harness.send_message_with_tools(_scene_message(scene))
    return builder.finish()


def _scene_message(scene: ScenePlan) -> str:
    """The planner's input: the whole scene plan, as it was decided.

    All of it, not a summary. The terrain question cannot be answered from the
    region list alone — what the objects are and how big they are is what says
    whether a ridge is a backdrop or a thing you walk up.
    """
    return (
        f"Scene plan for {scene.name!r}:\n"
        f"{json.dumps(scene.model_dump(exclude_none=True), indent=1)}\n\n"
        f"Plan the terrain for this world."
    )


def render_concept(
    plan: TerrainPlan,
    out: Path | str,
    *,
    images: ImageModel | None = None,
    width: int = 1216,
    height: int = 832,
    seed: int = 91,
) -> Path:
    """Render I_concept from the plan's concept paragraph.

    Wide rather than square, and framed as a photograph from height, because
    what it has to establish is landform and palette across a whole world.
    """
    images = images or ImageModel()
    return images.generate(
        f"A high aerial photograph of a landscape. {plan.concept} "
        f"Natural light, photographic, sharp, no people.",
        out,
        negative=CONCEPT_NEGATIVE,
        width=width,
        height=height,
        seed=seed,
    )


def terrain_stage(
    scene: ScenePlan,
    out_dir: Path | str,
    *,
    model: str = DEFAULT_MODEL,
) -> TerrainPlan:
    """Plan the terrain and render its concept image, writing both to `out_dir`.

    The plan is written to disk before the image is asked for: if that call
    fails, the expensive half survives and `render_concept` can be run against
    it later.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = plan_terrain(scene, model=model)
    (out_dir / "terrain_plan.json").write_text(plan.model_dump_json(indent=2))

    render_concept(plan, out_dir / "concept.png")
    return plan


__all__ = [
    "plan_terrain",
    "render_concept",
    "terrain_stage",
    "TERRAIN_PROMPT",
    "DEFAULT_MODEL",
]
