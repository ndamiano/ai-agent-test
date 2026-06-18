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
# The node loop has two genuinely different jobs — authoring fresh scenes vs. surgically
# repairing existing ones — that want different instructions (and different tools). One blob
# carrying both is noise the small model deliberates over; split by the active target's job.
_NODE_PROMPTS: Dict[str, str] = {
    "author": render_template(_PROMPTS_DIR / "write_node.txt", {}),
    "fix": render_template(_PROMPTS_DIR / "fix_node.txt", {}),
}
_MODE_PROMPTS: Dict[str, str] = {
    "premise": render_template(_PROMPTS_DIR / "mode_premise.txt", {}),
    "asset_manifest": render_template(_PROMPTS_DIR / "mode_asset.txt", {}),
    "node_scripts": _NODE_PROMPTS["author"],  # stateless fallback path: author is the default job
}
# Which job each node target is. Building/growing content = author; making existing nodes
# wire up or compile = fix. Mirrors _TARGET_TOOLS. Unlisted → author.
_TARGET_PROMPT: Dict[str, str] = {
    "count": "author", "each_node_min_lines": "author",
    "min_branches": "author", "all_characters_speak": "author",
    "reachable_from_start": "fix", "compiles": "fix",
}


def _prompt_for_target(target: Optional[Dict]) -> str:
    kind = _TARGET_PROMPT.get((target or {}).get("check", {}).get("type"), "author")
    return _NODE_PROMPTS[kind]
_MODE_TOOLS: Dict[str, frozenset] = {
    "premise": frozenset({"write_component", "update_scratchpad", "request_review"}),
    "asset_manifest": frozenset({"write_component", "update_scratchpad", "request_review"}),
    "node_scripts": frozenset({"write_node", "edit_node", "read_node", "read_story_state",
                               "validate", "compile_renpy", "update_scratchpad", "request_review"}),
}


def _schemas_for_mode(mode: Optional[str], all_schemas: List[Dict]) -> List[Dict]:
    allowed = _MODE_TOOLS.get(mode)
    if not allowed:
        return all_schemas
    return [s for s in all_schemas if s.get("function", {}).get("name") in allowed]


# Per-TARGET tool gating inside the node sub-loop. Each structural goal needs only a few
# tools; exposing the rest invites waste — while driving `count`, read_node/edit_node let the
# model fixate on an existing node (re-reading, futile edits) instead of writing new ones.
# Reachability is fixed by wiring orphans from existing nodes (edit only) — creating nodes
# makes it worse, so write_node is withheld there. Unlisted targets get the full node set.
_TARGET_TOOLS: Dict[str, frozenset] = {
    "count": frozenset({"write_node"}),
    "each_node_min_lines": frozenset({"read_node", "write_node", "edit_node"}),
    "reachable_from_start": frozenset({"read_node", "edit_node"}),
    "min_branches": frozenset({"read_node", "edit_node", "write_node"}),
    "all_characters_speak": frozenset({"read_node", "edit_node", "write_node"}),
    "compiles": frozenset({"read_node", "edit_node", "write_node", "compile_renpy"}),
}


def _schemas_for_target(target: Optional[Dict], node_schemas: List[Dict]) -> List[Dict]:
    allowed = _TARGET_TOOLS.get((target or {}).get("check", {}).get("type"))
    if not allowed:
        return node_schemas
    return [s for s in node_schemas if s.get("function", {}).get("name") in allowed]


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
    ]
    target = ctx.get("target")
    if target:
        lines += [
            "",
            f"YOUR TARGET — finish ONLY when THIS check passes: "
            f"[{target.get('component_id')}] {target['check'].get('type')}: {target.get('detail')}",
            "Make the change that clears it. Don't chase other to-do items.",
        ]
    lines += ["", f"SCRATCHPAD: {json.dumps(pad, ensure_ascii=False)}"]
    upstream = ctx.get("upstream") or {}
    if upstream:
        lines += [
            "",
            "LOCKED COMPONENTS (settled — use these EXACT ids, do not invent or rename):",
            json.dumps(upstream, ensure_ascii=False),
        ]
    view = ctx.get("active_view")
    if view and view.get("node_ids"):
        edges = view.get("edges", {})
        counts = view.get("line_counts", {})
        unreachable = set(view.get("unreachable", []))
        node_lines = [
            f"  {nid} -> {edges.get(nid, [])}"
            f"  ({'UNREACHABLE' if nid in unreachable else 'reachable'}, {counts.get(nid, 0)} lines)"
            for nid in view["node_ids"]
        ]
        lines += [
            "",
            "CURRENT NODES (these already exist — reuse these EXACT ids; jump ONLY to an id "
            "listed here or to a node you also create this step):",
            *node_lines,
        ]
    if ctx.get("story_state"):
        lines.append(f"STORY STATE: {json.dumps(ctx['story_state'], ensure_ascii=False)}")
    if ctx.get("last_read"):
        lines += ["", f"LAST READ:\n{ctx['last_read']}"]
    lines += [f"LAST RESULT: {ctx.get('last_result')}"]
    if ctx.get("stalled"):
        lines += [
            "",
            "⚠ You just repeated a read without changing anything. STOP reading — you have "
            "the content above. Call write_node or edit_node NOW to make a change.",
        ]
    lines += ["", "Call one tool to address the first to-do item."]
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
        # If the agent is spinning on reads, take read tools away so it must act.
        if context.get("stalled"):
            schemas = [s for s in schemas
                       if not s.get("function", {}).get("name", "").startswith("read")]
        messages = MessageBuilder(system).extend(
            [MessageBuilder.user_msg(_render_context(context))]).build()
        response = conn.generate_with_tools(messages, schemas)
        action = _parse_action(response)
        args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
        target = args.get("node_id") or args.get("component_id") or ""
        logger.info("decided: %s%s", action.get("tool", "(none)"), f"({target})" if target else "")
        return action

    return decide


def _parse_tool_args(tc: Dict) -> Dict:
    raw = (tc.get("function", {}).get("arguments") or "{}").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
        raw = raw.strip()
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("subloop returned unparseable args: %r", raw)
        return {}


def make_node_subloop(connector=None, component_guide: str = "", cap: int = 20) -> Callable:
    """A stateful sub-agent that drives ONE target check to green, with working memory.

    Unlike the stateless decider (one tool call from rebuilt context), this keeps a tool
    conversation: it reads/writes/edits and SEES its own results, looping until the target
    passes or it hits the step cap. The transcript holds only this task's tool calls (never
    the whole artifact) and MessageBuilder.build() enforces a char budget, so context stays
    bounded — the per-target reset is what keeps it from rotting as the game grows.
    """
    from llm_clients.connector_selector import get_connector
    conn = connector or get_connector()
    guide_suffix = f"\n\n{component_guide}" if component_guide else ""
    node_schemas = _schemas_for_mode("node_scripts", TOOL_SCHEMAS)

    def run(target, context, dispatch, target_met, report, budget, view_fn):
        ctx = dict(context)
        ctx["target"] = target
        # Gate tools AND the system prompt to THIS target's job: driving `count` exposes only
        # write_node with the author prompt; a fix target gets edit tools + the repair prompt.
        schemas = _schemas_for_target(target, node_schemas)
        system = _prompt_for_target(target) + guide_suffix
        mb = MessageBuilder(system).add_user(_render_context(ctx))

        for _ in range(min(cap, max(budget, 0))):
            response = conn.generate_with_tools(mb.build(), schemas)
            if "error" in response:
                logger.warning("subloop LLM error: %s", response["error"])
                break
            message = (response.get("choices") or [{}])[0].get("message", {})
            tool_calls = [tc for tc in (message.get("tool_calls") or [])
                          if tc.get("function", {}).get("name")]
            if not tool_calls:
                break

            mb.add_assistant(message.get("content"), tool_calls=tool_calls)
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = _parse_tool_args(tc)
                result = dispatch({"tool": name, "args": args})
                mb.add_tool_result(tc.get("id", ""),
                                   json.dumps(result, ensure_ascii=False)[:800])
                tgt = args.get("node_id") or args.get("component_id") or ""
                err = result.get("error") if isinstance(result, dict) else None
                report(f"{name}({tgt}): " + (f"error — {err}" if err else "ok"))

            # The artifact changed; re-show the graph so the agent tracks ids as it builds.
            view = view_fn()
            if view and view.get("node_ids"):
                note = "CURRENT NODES: " + json.dumps(view["node_ids"], ensure_ascii=False)
                if view.get("unreachable"):
                    note += f" | UNREACHABLE: {view['unreachable']}"
                mb.add_user(note)

            if target_met():
                break

    return run
