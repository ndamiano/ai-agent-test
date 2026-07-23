"""LLM tool-call parsing helpers shared by the codegen fix shapes.

Local models are loose about tool calls — arguments arrive already-parsed, wrapped in a stray single
key, or as raw JSON in message content instead of a function call. `parse_args` normalizes any
argument shape to a dict; `salvage_tool_call` rebuilds a call from content JSON when it uniquely fits
one offered tool's parameters, so the work isn't thrown away.

(The old `Services` gateway + `AgentLoop` that owned the synchronous fix loop are gone — a build is
now a chain of `llm` jobs driven by build_chain, so nothing dispatches "through Services" any more.)
"""

import json
import logging
from typing import Dict, Optional

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


def salvage_tool_call(content: str, schemas) -> Optional[Dict]:
    """Local models sometimes emit a tool's arguments as raw JSON in message content instead of as a
    function call. If that JSON uniquely fits one available tool's parameters, rebuild the call so the
    work isn't thrown away (and we skip a wasted nudge round-trip). Bail when ambiguous — let the
    nudge path handle it."""
    args = parse_args(content) if content else {}
    if not isinstance(args, dict) or not args:
        return None
    keys = set(args)
    matches = []
    for s in schemas or []:
        f = s.get("function", {})
        params = f.get("parameters", {}) or {}
        props = set(params.get("properties", {}) or {})
        required = set(params.get("required", []) or [])
        if required <= keys <= props:
            matches.append(f.get("name"))
    if len(matches) != 1:
        return None
    return {"id": "salvaged", "type": "function",
            "function": {"name": matches[0], "arguments": json.dumps(args, ensure_ascii=False)}}
