import sys as _sys
import pathlib as _pl
_src = _pl.Path(__file__).resolve().parent.parent / "src"
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))

import json
import logging
from typing import Optional

from llm_clients.connector_selector import get_connector
from llm_clients.message_builder import MessageBuilder
from llm_clients.inference import strip_fences

logger = logging.getLogger(__name__)

_SCORE_SYSTEM = (
    "You evaluate AI-generated creative content against a rubric. "
    "Score each criterion 0–100 to one decimal place.  "
    "90–100: exceptional, would impress a professional. "
    "60–89: adequate but unremarkable. "
    "30–59: noticeably weak, needs improvement. "
    "0–29: seriously flawed or missing. "
    "most AI output is mediocre. A score above 60 requires specific evidence from the output. "
    "If a criterion is clearly failing, score it 20–40 regardless of other strengths. "
    "Return only valid JSON, no other text."
)

_MUTATE_SYSTEM = (
    "You improve prompt templates used in AI creative pipelines. "
    "Clear structure, concrete examples, and tight scope produce better output "
    "than vague instructions. "
    "Focus on structural changes: how the task is framed, what the model is told to prioritize, "
    "how the output is structured. "
    "Never hardcode content from specific examples — no character names, place names, "
    "story details, or genre-specific references. Prompts must work across many different briefs. "
    "Return only the improved prompt text, nothing else."
)


def _has_axes(rubric: dict) -> bool:
    return any("axis" in c for c in rubric.get("criteria", []))


def _criteria_block(rubric: dict) -> str:
    """Render criteria for the score prompt, grouped by axis when the rubric defines them."""
    if not _has_axes(rubric):
        return "\n".join(
            f"- {c['name']} (weight {c.get('weight', 1.0)}): {c['description']}"
            for c in rubric["criteria"]
        )
    lines = []
    for axis in rubric.get("axes") or _axes_in_order(rubric):
        lines.append(f"\n{axis.upper()} axis:")
        lines += [
            f"  - {c['name']}: {c['description']}"
            for c in rubric["criteria"] if c.get("axis") == axis
        ]
    return "\n".join(lines)


def _axes_in_order(rubric: dict) -> list:
    seen = []
    for c in rubric.get("criteria", []):
        a = c.get("axis")
        if a and a not in seen:
            seen.append(a)
    return seen


def _score_prompt(stage_id: str, output: dict, rubric: dict) -> str:
    score_schema = {
        c["name"]: {"score": "float 0–100", "reasoning": "one sentence citing specific evidence from the output"}
        for c in rubric["criteria"]
    }
    if _has_axes(rubric):
        overall_note = (
            "Score every criterion independently. The overall is the mean of the axis means "
            "(each axis weighted equally); compute it that way, not as a flat average of all criteria."
        )
    else:
        overall_note = "float 0–100 (weighted average across criteria)"
    score_schema["overall"] = overall_note
    if isinstance(output, str):
        heading, body = "Artifact to evaluate (the built game script a player runs):", output
    else:
        heading, body = "Output to evaluate:", json.dumps(output, indent=2, ensure_ascii=False)
    lines = [f"Stage: {stage_id}", ""]
    if rubric.get("note"):
        lines += [rubric["note"], ""]
    lines += [
        heading,
        body,
        "",
        "Criteria:",
        _criteria_block(rubric),
        "",
        "Return JSON exactly matching this structure:",
        json.dumps({"scores": score_schema}, indent=2),
    ]
    return "\n".join(lines)


def _mutate_prompt(current_prompt: str, summary: dict, rubric: dict,
                   scored: list | None = None) -> str:
    by_criterion = summary.get("by_criterion", {})
    weakest = min(by_criterion, key=lambda k: by_criterion[k].get("p25", 5.0), default=None)
    if weakest is None:
        target_desc = "overall quality"
        weak_score = summary.get("overall", {}).get("p25", "unknown")
    else:
        weak_score = by_criterion[weakest].get("p25", "unknown")
        weak_crit = next((c for c in rubric["criteria"] if c["name"] == weakest), {})
        target_desc = f"'{weakest}' — {weak_crit.get('description', '')}"

    # Collect judge reasoning from the lowest-scoring runs for the weakest criterion
    examples = []
    if scored and weakest:
        runs = [
            (float(s["scores"][weakest]["score"]), s["scores"][weakest].get("reasoning", ""))
            for s in scored
            if s and weakest in s.get("scores", {}) and s["scores"][weakest].get("reasoning")
        ]
        runs.sort(key=lambda x: x[0])
        examples = [f"score {sc:.0f}/100: {r}" for sc, r in runs[:3]]

    lines = [f"This prompt template currently scores {weak_score} on {target_desc}."]
    if examples:
        lines += ["", "Judge reasoning from the weakest runs (lowest first):"]
        lines += [f"  - {ex}" for ex in examples]
    lines += [
        "",
        "Current prompt:",
        "---",
        current_prompt,
        "---",
        "",
        f"Rewrite this prompt to score higher on {target_desc}.",
        "Keep all template variables (e.g. {{genre}}, {{tone}}) intact.",
        "Return only the rewritten prompt text, nothing else.",
    ]
    return "\n".join(lines)


def axis_means(scores: dict, rubric: dict) -> dict:
    """Mean criterion score per axis, for axis-structured rubrics."""
    means = {}
    for axis in _axes_in_order(rubric):
        vals = [
            float(scores[c["name"]]["score"])
            for c in rubric["criteria"]
            if c.get("axis") == axis and c["name"] in scores
        ]
        if vals:
            means[axis] = round(sum(vals) / len(vals), 2)
    return means


def _axis_overall(scores: dict, rubric: dict) -> float:
    """Overall = mean of the per-axis means (each axis weighted equally)."""
    means = axis_means(scores, rubric)
    if not means:
        return 0.0
    return round(sum(means.values()) / len(means), 2)


def _weighted_average(scores: dict, rubric: dict) -> float:
    total_weight = sum(c.get("weight", 1.0) for c in rubric["criteria"])
    if not total_weight:
        return 0.0
    weighted = sum(
        float(scores.get(c["name"], {}).get("score", 0)) * c.get("weight", 1.0)
        for c in rubric["criteria"]
        if c["name"] in scores
    )
    return round(weighted / total_weight, 2)


class Judge:
    def __init__(self, connector=None):
        self._connector = connector or get_connector()

    def _call(self, system: str, user: str, json_mode: bool = False) -> str:
        messages = MessageBuilder(system).extend([MessageBuilder.user_msg(user)]).build()
        fmt = {"type": "json_object"} if json_mode else None
        result = self._connector.generate_with_tools(messages, [], response_format=fmt)
        if "error" in result:
            raise RuntimeError(f"Judge LLM error: {result['error']}")
        return result["choices"][0]["message"]["content"].strip()

    def score(self, stage_id: str, output: dict, rubric: dict) -> Optional[dict]:
        try:
            raw = self._call(_SCORE_SYSTEM, _score_prompt(stage_id, output, rubric), json_mode=True)
            parsed = json.loads(strip_fences(raw))
            scores = parsed.get("scores", {k: v for k, v in parsed.items() if k != "overall"})
            if _has_axes(rubric):
                overall = _axis_overall(scores, rubric)
            else:
                overall = parsed.get("overall") or _weighted_average(scores, rubric)
            return {"scores": scores, "overall": float(overall)}
        except Exception as e:
            logger.warning(f"Judge scoring failed: {e}")
            return None

    def propose_mutation(self, current_prompt: str, summary: dict, rubric: dict,
                         scored: list | None = None) -> str:
        return self._call(_MUTATE_SYSTEM, _mutate_prompt(current_prompt, summary, rubric, scored))
