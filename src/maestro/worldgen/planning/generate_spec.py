"""Run stage 1: intent analysis, then scene-level planning.

Two agents, two schemas. The first extracts what the user stated and stops
there; the second completes the rest, conditioned on the prompt *and* that
extraction. Keeping them apart is what stops invented detail from being
mistaken for a user requirement.

    intent = analyze_intent("Build me a desert battlefield.")
    plan = plan_scene(intent)

or, for both at once:

    plan = generate_spec("Build me a desert battlefield.")
"""
from __future__ import annotations

import json
from pathlib import Path

from ..llm import LLMHarness
from .models import Intent, ScenePlan
from .tools import IntentBuilder, PlanBuilder

_HERE = Path(__file__).parent
INTENT_PROMPT = (_HERE / "intent_prompt.txt").read_text().strip()
PLAN_PROMPT = (_HERE / "plan_prompt.txt").read_text().strip()


def analyze_intent(
    prompt: str,
    *,
    temperature: float = 0.2,
) -> Intent:
    """Extract the constraints `prompt` explicitly states.

    Runs cool by default: this stage should be reading, not inventing.
    """
    builder = IntentBuilder(prompt)
    harness = LLMHarness(
        tools=builder.tools,
        system=INTENT_PROMPT,
        temperature=temperature,
    )
    harness.send_message_with_tools(prompt)
    return builder.finish()


def plan_scene(
    intent: Intent,
    *,
    temperature: float = 0.7,
) -> ScenePlan:
    """Complete `intent` into a full scene plan, preserving its constraints."""
    builder = PlanBuilder()
    harness = LLMHarness(
        tools=builder.tools,
        system=PLAN_PROMPT,
        temperature=temperature,
    )
    harness.send_message_with_tools(_plan_message(intent))
    return builder.finish()


def _plan_message(intent: Intent) -> str:
    """The planning agent's input: the original prompt plus the extracted constraints."""
    constraints = json.dumps(
        intent.model_dump(exclude={"prompt"}, exclude_none=True), indent=2
    )
    return (
        f"User prompt:\n{intent.prompt}\n\n"
        f"Constraints the user explicitly stated (everything else is yours to "
        f"decide):\n{constraints}"
    )


def generate_spec(
    prompt: str,
    *,
    intent_temperature: float = 0.2,
    plan_temperature: float = 0.7,
) -> ScenePlan:
    """Run both stages and return the scene plan.

    Use `analyze_intent` and `plan_scene` directly to inspect or edit the
    extracted constraints in between.
    """
    intent = analyze_intent(prompt, temperature=intent_temperature)
    return plan_scene(intent, temperature=plan_temperature)


__all__ = [
    "generate_spec",
    "analyze_intent",
    "plan_scene",
    "INTENT_PROMPT",
    "PLAN_PROMPT",
]
