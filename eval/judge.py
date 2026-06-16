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
    "You are a prefessional judge, who has played many visual novels."
    "Your goal is to score a visual novel between 0 and 4 extremely critically.  "
    "The author is relying on your ouput to get better, so score it harshly, and explain why and how to improve it.  "
    "When you give a score, explain why you did not give a lower score.  "
    "Return only valid JSON, no other text."
)

def _has_axes(rubric: dict) -> bool:
    return any("axis" in c for c in rubric.get("criteria", []))


def _is_levels(rubric: dict) -> bool:
    return any("levels" in c for c in rubric.get("criteria", []))


def _level_keys(rubric: dict) -> list:
    lo = rubric.get("scale", {}).get("min", 0)
    hi = rubric.get("scale", {}).get("max", 4)
    return [str(i) for i in range(lo, hi + 1)]


def _criteria_block(rubric: dict) -> str:
    """Render criteria for the score prompt, grouped by axis when the rubric defines them."""
    if not _has_axes(rubric):
        if _is_levels(rubric):
            keys = _level_keys(rubric)
            lines = []
            for c in rubric["criteria"]:
                lines.append(f"- {c['name']} — {c.get('description', '')}")
                lines += [f"    {k}: {c['levels'][k]}" for k in keys if k in c.get("levels", {})]
            return "\n".join(lines)
        return "\n".join(
            f"- {c['name']} (weight {c.get('weight', 1.0)}): {c['description']}"
            for c in rubric["criteria"]
        )
    levels = _is_levels(rubric)
    keys = _level_keys(rubric)
    lines = []
    for axis in rubric.get("axes") or _axes_in_order(rubric):
        lines.append(f"\n{axis.upper()} axis:")
        for c in rubric["criteria"]:
            if c.get("axis") != axis:
                continue
            if levels and "levels" in c:
                lines.append(f"  {c['name']} — {c.get('description', '')}")
                lines += [f"    {k}: {c['levels'][k]}" for k in keys if k in c["levels"]]
            else:
                lines.append(f"  - {c['name']}: {c['description']}")
    return "\n".join(lines)


def _axes_in_order(rubric: dict) -> list:
    seen = []
    for c in rubric.get("criteria", []):
        a = c.get("axis")
        if a and a not in seen:
            seen.append(a)
    return seen


def _score_prompt(stage_id: str, output: dict, rubric: dict) -> str:
    if _is_levels(rubric):
        lo, hi = rubric.get("scale", {}).get("min", 0), rubric.get("scale", {}).get("max", 4)
        score_schema = {
            c["name"]: {"level": f"integer {lo}-{hi}",
                        "reasoning": "quote specific evidence from the script, then explain "
                                     "why this does not deserve the next level down"}
            for c in rubric["criteria"]
        }
    else:
        score_schema = {
            c["name"]: {"score": "float 0–100", "reasoning": "cite specific evidence from the output, "
                        "then explain why it does not deserve a lower score"}
            for c in rubric["criteria"]
        }
        if _has_axes(rubric):
            score_schema["overall"] = (
                "Score every criterion independently. The overall is the mean of the axis means "
                "(each axis weighted equally); compute it that way, not as a flat average of all criteria."
            )
        else:
            score_schema["overall"] = "float 0–100 (weighted average across criteria)"
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


def _levels_to_scores(raw: dict, rubric: dict) -> dict:
    """Map the judge's per-criterion level (0-max) to a 0-100 score via the display multiplier.

    Keeps the raw level alongside so reasoning output can show it. Tolerates the model
    returning the level under "level" or "score", as int or string.
    """
    mult = rubric.get("scale", {}).get("display_multiplier", 25)
    hi = rubric.get("scale", {}).get("max", 4)
    scores = {}
    for c in rubric["criteria"]:
        name = c["name"]
        entry = raw.get(name)
        if not isinstance(entry, dict):
            continue
        val = entry.get("level", entry.get("score"))
        try:
            level = int(round(float(val)))
        except (TypeError, ValueError):
            continue
        level = max(0, min(hi, level))
        scores[name] = {"score": level * mult, "level": level,
                        "reasoning": entry.get("reasoning", "")}
    return scores


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


def _overall(scores: dict, rubric: dict) -> float:
    """Mean of axis means for axis rubrics; weighted average for flat ones."""
    return _axis_overall(scores, rubric) if _has_axes(rubric) else _weighted_average(scores, rubric)


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
            raw = parsed.get("scores", {k: v for k, v in parsed.items() if k != "overall"})
            if _is_levels(rubric):
                scores = _levels_to_scores(raw, rubric)
                overall = _overall(scores, rubric)
            else:
                scores = raw
                if _has_axes(rubric):
                    overall = _axis_overall(scores, rubric)
                else:
                    overall = parsed.get("overall") or _weighted_average(scores, rubric)
            return {"scores": scores, "overall": float(overall)}
        except Exception as e:
            logger.warning(f"Judge scoring failed: {e}")
            return None
