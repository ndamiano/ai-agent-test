"""The build agent — the LLM that chooses the next action each executor step.

Stateless per step: every call gets a freshly rebuilt context (spec + to-do +
scratchpad + last result) and no transcript. It returns ONE tool call. The
executor decides completion via validate, so the agent never claims done.
"""

import json
import logging
from typing import Callable, Dict, List, Optional

from llm_clients.message_builder import MessageBuilder
from maestro.tools import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are building a Ren'Py visual novel against a frozen spec. Each turn you "
    "see the spec, the current to-do (done-conditions that still fail), your "
    "scratchpad, and the last result. Choose exactly ONE tool call that clears the "
    "next failing condition. You author component content yourself as JSON via "
    "write_component. Keep working until the to-do is empty. Do not explain — call a tool."
)


def _render_context(ctx: Dict) -> str:
    todo = ctx.get("todo", [])
    todo_lines = [f"- [{f['component_id']}] {f['check'].get('type')}: {f.get('detail')}"
                  for f in todo] or ["(none — build may be complete)"]
    pad = ctx.get("scratchpad", {})
    return "\n".join([
        f"SPEC: {json.dumps(ctx.get('spec', {}), ensure_ascii=False)}",
        "",
        "TO-DO (failing done-conditions):",
        *todo_lines,
        "",
        f"SCRATCHPAD: {json.dumps(pad, ensure_ascii=False)}",
        f"LAST RESULT: {ctx.get('last_result')}",
        "",
        "Call one tool to address the first to-do item.",
    ])


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


def make_llm_decider(tool_schemas: Optional[List[Dict]] = None, connector=None) -> Callable:
    from llm_clients.connector_selector import get_connector
    conn = connector or get_connector()
    schemas = tool_schemas or TOOL_SCHEMAS

    def decide(context: Dict) -> Dict:
        messages = MessageBuilder(_SYSTEM).extend(
            [MessageBuilder.user_msg(_render_context(context))]).build()
        response = conn.generate_with_tools(messages, schemas)
        return _parse_action(response)

    return decide
