"""LLM tool-call argument parsing, shared by the build turn machine.

Local models are loose about tool calls: arguments arrive already-parsed, or wrapped in a stray
single key. `parse_args` normalizes any argument shape to a dict, and `parse_args_checked` also says
whether anything could be read at all — a call cut off mid-argument and a call that carries no
arguments both answer {}, and only one of them is a failure.

Recovering a call the model wrote as TEXT is `maestro.tool_calls` — it owns every encoding.
"""

import json
import logging
from typing import Dict, Tuple


logger = logging.getLogger(__name__)


def parse_args(raw) -> Dict:
    return parse_args_checked(raw)[0]


def parse_args_checked(raw) -> Tuple[Dict, bool]:
    """(arguments, readable). `readable` is False only when there was text to read and none of it
    parsed — which is what a call cut off at the output cap looks like."""
    # Some templates (observed: gemma via llama.cpp) hand arguments back already-parsed, or
    # mangled into a dict whose single KEY is the JSON blob — normalize every shape to a dict.
    if isinstance(raw, dict):
        if len(raw) == 1:
            k, v = next(iter(raw.items()))
            if k.lstrip().startswith("{") and v in ("", None):
                return parse_args_checked(k)
        return raw, True
    raw = (raw or "{}").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
        raw = raw.strip()
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        # A complete object with something after it is a whole call the model MEANT — observed
        # 2026-09-10 on DeepSeek Flash, which appended text past the closing brace. Take the
        # object; only a leading fragment that never closes is unreadable.
        try:
            parsed, _ = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            logger.warning("unparseable tool args: %r", raw)
            return {}, False
    return (parsed, True) if isinstance(parsed, dict) else ({}, False)
