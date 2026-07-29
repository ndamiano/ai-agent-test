"""LLM tool-call argument parsing, shared by the build turn machine.

Local models are loose about tool calls: arguments arrive already-parsed, or wrapped in a stray
single key. `parse_args` normalizes any argument shape to a dict.

Recovering a call the model wrote as TEXT is `maestro.tool_calls` — it owns every encoding.
"""

import json
import logging
from typing import Dict


logger = logging.getLogger(__name__)


def parse_args(raw) -> Dict:
    # Some templates (observed: gemma via llama.cpp) hand arguments back already-parsed, or
    # mangled into a dict whose single KEY is the JSON blob — normalize every shape to a dict.
    if isinstance(raw, dict):
        if len(raw) == 1:
            k, v = next(iter(raw.items()))
            if k.lstrip().startswith("{") and v in ("", None):
                return parse_args(k)
        return raw
    raw = (raw or "{}").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
        raw = raw.strip()
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("unparseable tool args: %r", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}
