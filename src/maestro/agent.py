"""The build agent — the LLM that chooses the next action each executor step.

Stateless per step: every call gets a freshly rebuilt context (spec + to-do +
scratchpad + last result) and no transcript. It returns ONE tool call. The
executor decides completion via validate, so the agent never claims done.
"""

import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from llm_clients.message_builder import MessageBuilder
from maestro.tools import TOOL_SCHEMAS
from renpy.templating import render_template

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SYSTEM = render_template(_PROMPTS_DIR / "build_agent_system.txt", {})

# Per-stage modes: a still-failing component can get a tighter system prompt + a
# restricted tool set. The executor picks the mode (context["mode"]); an unknown/None
# mode falls back to the general prompt + full tools, so nothing regresses.
_MODE_PROMPTS: Dict[str, str] = {
    "premise": render_template(_PROMPTS_DIR / "mode_premise.txt", {}),
    "asset_manifest": render_template(_PROMPTS_DIR / "mode_asset.txt", {}),
    "node_scripts": render_template(_PROMPTS_DIR / "mode_node.txt", {}),
}
_MODE_TOOLS: Dict[str, frozenset] = {
    "premise": frozenset({"write_component", "update_scratchpad", "request_review"}),
    "asset_manifest": frozenset({"write_component", "update_scratchpad", "request_review"}),
    "node_scripts": frozenset({"write_node", "edit_node", "read_story_state", "validate",
                               "compile_renpy", "update_scratchpad", "request_review"}),
}


def _schemas_for_mode(mode: Optional[str], all_schemas: List[Dict]) -> List[Dict]:
    allowed = _MODE_TOOLS.get(mode)
    if not allowed:
        return all_schemas
    return [s for s in all_schemas if s.get("function", {}).get("name") in allowed]


def _render_context(ctx: Dict) -> str:
    todo = ctx.get("todo", [])
    todo_lines = [f"- [{f['component_id']}] {f['check'].get('type')}: {f.get('detail')}"
                  for f in todo] or ["(none — build may be complete)"]
    pad = ctx.get("scratchpad", {})
    lines = [
        f"SPEC: {json.dumps(ctx.get('spec', {}), ensure_ascii=False)}",
        "",
        "TO-DO (failing done-conditions):",
        *todo_lines,
        "",
        f"SCRATCHPAD: {json.dumps(pad, ensure_ascii=False)}",
    ]
    upstream = ctx.get("upstream") or {}
    if upstream:
        lines += [
            "",
            "LOCKED COMPONENTS (settled — use these EXACT ids, do not invent or rename):",
            json.dumps(upstream, ensure_ascii=False),
        ]
    if ctx.get("story_state"):
        lines.append(f"STORY STATE: {json.dumps(ctx['story_state'], ensure_ascii=False)}")
    lines += [
        f"LAST RESULT: {ctx.get('last_result')}",
        "",
        "Call one tool to address the first to-do item.",
    ]
    return "\n".join(lines)


def _parse_action(response: Dict) -> Dict:
    if "error" in response:
        logger.warning("decider LLM error: %s", response["error"])
        return {}
    message = (response.get("choices") or [{}])[0].get("message", {})
    tool_calls = [tc for tc in (message.get("tool_calls") or [])
                  if tc.get("function", {}).get("name")]
    if not tool_calls:
        return {}
    fn = tool_calls[0]["function"]
    raw = (fn.get("arguments") or "{}").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
        raw = raw.strip()
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("decider returned unparseable args: %r", raw)
        args = {}
    return {"tool": fn["name"], "args": args}


def make_llm_decider(tool_schemas: Optional[List[Dict]] = None, connector=None,
                     component_guide: str = "") -> Callable:
    from llm_clients.connector_selector import get_connector
    conn = connector or get_connector()
    all_schemas = tool_schemas or TOOL_SCHEMAS
    guide_suffix = f"\n\n{component_guide}" if component_guide else ""

    def decide(context: Dict) -> Dict:
        mode = context.get("mode")
        system = _MODE_PROMPTS.get(mode, _SYSTEM) + guide_suffix
        schemas = _schemas_for_mode(mode, all_schemas)
        messages = MessageBuilder(system).extend(
            [MessageBuilder.user_msg(_render_context(context))]).build()
        response = conn.generate_with_tools(messages, schemas)
        action = _parse_action(response)
        args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
        target = args.get("node_id") or args.get("component_id") or ""
        logger.info("decided: %s%s", action.get("tool", "(none)"), f"({target})" if target else "")
        return action

    return decide
