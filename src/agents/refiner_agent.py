"""Refiner agent — conversational goal refinement before task execution."""

import json
import logging
from typing import List, Dict

from llm_clients.connector_selector import get_connector
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

_CHAT_SYSTEM_PROMPT = """\
You are a design collaborator helping the user think through a task before an AI agent executes it. Your job is to build up a rich, specific picture of what they want — then that picture becomes the agent's instructions.

How to behave:
- Engage substantively with whatever the user raises. If they mention characters, explore characters — offer concrete options (e.g. distinct stats vs. personality-driven dialogue vs. both), ask what matters most to them, and build on their answers before moving on.
- Proactively surface the key dimensions of their task that haven't been discussed yet. For a game: platform, genre, mechanics, characters, visual style, win condition, scope. For a web app: stack, data model, key flows, auth, deployment. Don't exhaustively cover every dimension — focus on the ones that will most affect how the work gets done.
- Offer suggestions and options, not just open questions. "Are you thinking X or Y?" is better than "What are you thinking?" Give them something to react to.
- You can ask 2-3 things in one response when they're closely related, but don't dump a long list of questions. Prioritize.
- Match their energy. If they give short answers, keep moving. If they want to go deep on something, go deep.
- When the picture feels specific enough to act on — you have a clear goal, key constraints, and enough detail to write concrete acceptance criteria — say so and invite them to finalize.
- Never start executing the task. Your only job is to help define it well.
"""

_SYNTHESIS_SYSTEM_PROMPT = """\
You are a task synthesis assistant. Given a conversation where a user and a collaborator have designed a task together, distill everything into a structured JSON output that an AI agent can execute without any ambiguity.

Output ONLY valid JSON with this exact shape:
{
  "refined_goal": "<a detailed, self-contained description of what needs to be built or done>",
  "acceptance_criteria": [
    "<testable condition 1>",
    "<testable condition 2>",
    ...
  ]
}

Rules for refined_goal:
- A single dense paragraph capturing full intent — what it is, key design decisions, constraints, tech choices, and scope.
- Someone reading this with no context should know exactly what to build.

Rules for acceptance_criteria — READ CAREFULLY:
- Each criterion is a PASS/FAIL TEST. A human reviewer must be able to check it without interpretation.
- Write criteria as observable outcomes, not implementation tasks.
- BANNED words and phrases: "implement", "develop", "create", "ensure", "support", "include", "provide", "build", "make sure", "should have", "the system does". If you are about to write one of these, stop and reframe as an observable result.
- Instead, use: "When X happens, Y is the result", "Given Z, the output is...", "The [thing] displays/returns/stores/rejects...", specific named values, counts, behaviors, formats.

BAD (too vague, fails the test):
  "Implement a tiered currency system where enemies drop unique essences"
GOOD (observable, pass/fail):
  "Killing a Tier-1 (Lower Mortal) enemy always drops exactly one Iron Essence; killing a Tier-3 (Astral Sea) enemy drops one Void Shard; no enemy drops both types in a single kill"

BAD:
  "Ensure the player can upgrade their stats"
GOOD:
  "Spending 10 Iron Essences at the upgrade screen permanently increases Strength by exactly 5 points, and the new value persists across save/load"

BAD:
  "Create a body mutation mechanic that replaces gear"
GOOD:
  "The inventory screen has no equipment slots; the character sheet instead lists up to 6 active mutations, each showing its name, stat delta (+X Strength / +Y Cosmic Reach), and one associated active or passive ability"

- Derive criteria from specifics actually discussed in the conversation — named systems, values, behaviors, UI flows, edge cases.
- Aim for 5-10 criteria. Fewer precise ones are better than many vague ones.
- Do not include any text outside the JSON object.
"""


class RefinerAgent:

    def generate_response(self, goal: str, history: List[Dict]) -> str:
        """Generate next conversational response given goal + message history."""
        connector = get_connector()

        # Always anchor with the original goal as the first user turn, then the
        # conversation history. This ensures the message order is always valid
        # (system → user → assistant → user → ...) regardless of history state.
        messages = [
            {"role": "system", "content": _CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": goal},
        ]
        messages.extend(history)

        try:
            result = connector.generate_with_tools(messages, [])
            if "error" in result:
                raise RuntimeError(result["error"])
            content = result.get("choices", [{}])[0].get("message", {}).get("content") or ""
            return content.strip()
        except Exception as e:
            logger.error(f"RefinerAgent.generate_response failed: {e}")
            raise

    def synthesize(self, goal: str, history: List[Dict]) -> Dict:
        """
        Given the full refinement conversation, produce a structured output with:
          - refined_goal: enriched goal string
          - acceptance_criteria: list of testable criteria strings
        """
        connector = get_connector()

        conversation_text = f"Original goal: {goal}\n\nRefinement conversation:\n"
        for msg in history:
            role = "User" if msg["role"] == "user" else "Assistant"
            conversation_text += f"\n{role}: {msg['content']}"

        messages = [
            {"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": conversation_text},
        ]

        try:
            result = connector.generate_with_tools(messages, [])
            if "error" in result:
                raise RuntimeError(result["error"])
            content = (result.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()

            # Strip markdown code fences if present
            content = content.replace("```json", "").replace("```", "").strip()

            parsed = json.loads(content)
            refined_goal = parsed.get("refined_goal", goal)
            criteria = parsed.get("acceptance_criteria", [])

            if not isinstance(criteria, list):
                criteria = []

            return {"refined_goal": refined_goal, "acceptance_criteria": criteria}

        except json.JSONDecodeError as e:
            logger.error(f"RefinerAgent.synthesize: failed to parse LLM output as JSON: {e}")
            # Graceful fallback — return original goal with no criteria
            return {"refined_goal": goal, "acceptance_criteria": []}
        except Exception as e:
            logger.error(f"RefinerAgent.synthesize failed: {e}")
            raise
