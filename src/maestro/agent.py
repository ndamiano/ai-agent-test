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

# Reads carry no artifact change; a run of them is the sub-loop sightseeing, not progressing.
_READ_TOOLS = {"read_node", "read_place", "read_component", "read_story_state"}

# Cap every build LLM call. One node/component is small (<~2k tokens); the connector default is
# 50k, which lets the small model's "dump the whole thing as prose instead of a tool call"
# failure mode burn minutes on a single dead step (observed: 265s / 49999 tokens). Bounding it
# turns a runaway into a quick dead turn the sub-loop's nudge then recovers from.
_BUILD_MAX_TOKENS = 8000

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
_PLACE_PROMPTS: Dict[str, str] = {
    "author": render_template(_PROMPTS_DIR / "write_place.txt", {}),
    "fix": render_template(_PROMPTS_DIR / "fix_place.txt", {}),
}
_MODE_PROMPTS: Dict[str, str] = {
    "premise": render_template(_PROMPTS_DIR / "mode_premise.txt", {}),
    "asset_manifest": render_template(_PROMPTS_DIR / "mode_asset.txt", {}),
    "matches": render_template(_PROMPTS_DIR / "mode_matches.txt", {}),
    "nodes": _NODE_PROMPTS["author"],  # stateless fallback path: author is the default job
    "places": _PLACE_PROMPTS["author"],
}
# Which job each target is. Building/growing content = author; making existing items
# wire up or compile = fix. Mirrors the *_TARGET_TOOLS maps. Unlisted → author.
_TARGET_PROMPT: Dict[str, str] = {
    "count": "author", "each_node_min_lines": "author",
    "min_branches": "author", "all_characters_speak": "author",
    "reachable_from_start": "fix", "node_targets_resolve": "fix", "compiles": "fix",
}
_PLACE_TARGET_PROMPT: Dict[str, str] = {
    "count": "author", "each_place_min_interactables": "author",
    "items_obtainable": "author", "items_used": "author",
    "places_reachable": "fix", "goal_reachable": "fix",
    "compiles": "fix",
}


def _prompt_for_target(target: Optional[Dict], prompts: Dict[str, str],
                       target_prompt: Dict[str, str]) -> str:
    kind = target_prompt.get((target or {}).get("check", {}).get("type"), "author")
    return prompts[kind]
_MODE_TOOLS: Dict[str, frozenset] = {
    "premise": frozenset({"write_component", "update_scratchpad", "request_review"}),
    "asset_manifest": frozenset({"write_component", "update_scratchpad", "request_review"}),
    "matches": frozenset({"write_component", "write_match", "read_component",
                          "update_scratchpad", "request_review"}),
    "nodes": frozenset({"write_node", "edit_node", "read_node", "read_story_state",
                        "validate", "update_scratchpad", "request_review"}),
    "places": frozenset({"write_component", "write_place", "edit_place", "add_interactable",
                         "read_place", "set_places_meta", "read_component", "validate",
                         "update_scratchpad", "request_review"}),
}


def _schemas_for_mode(mode: Optional[str], all_schemas: List[Dict]) -> List[Dict]:
    allowed = _MODE_TOOLS.get(mode)
    if not allowed:
        return all_schemas
    return [s for s in all_schemas if s.get("function", {}).get("name") in allowed]


# Per-TARGET tool gating inside the node sub-loop. Each structural goal needs only a few tools;
# exposing the rest invites waste — while driving `count`, read/edit let the model fixate on an
# existing node instead of writing new ones. Reachability is fixed by repointing an existing
# node's end (edit only), so write_node is withheld there. Unlisted targets get the full set.
_TARGET_TOOLS: Dict[str, frozenset] = {
    "count": frozenset({"write_node"}),
    "each_node_min_lines": frozenset({"read_node", "write_node", "edit_node"}),
    "reachable_from_start": frozenset({"read_node", "edit_node"}),
    "node_targets_resolve": frozenset({"read_node", "edit_node", "write_node"}),
    "min_branches": frozenset({"read_node", "edit_node", "write_node"}),
    "all_characters_speak": frozenset({"read_node", "edit_node", "write_node"}),
    "compiles": frozenset({"read_node", "edit_node", "write_node"}),
}
# Per-TARGET gating for the places sub-loop. count ADDS places (write_place); reachability/goal
# are fixed by repointing actions (edit), so write_place is withheld where adding would not help.
_PLACE_TARGET_TOOLS: Dict[str, frozenset] = {
    "count": frozenset({"write_component", "write_place"}),
    "each_place_min_interactables": frozenset({"read_place", "add_interactable", "edit_place"}),
    "items_obtainable": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta"}),
    "items_used": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta"}),
    "places_reachable": frozenset({"read_place", "add_interactable", "edit_place", "read_component"}),
    "goal_reachable": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta", "read_component"}),
    # compiles fixes are often cross-component: a dangling talk-node is repointed (edit_place), a
    # missing variable/flag is declared (set_places_meta), a missing hotspot added (add_interactable).
    "compiles": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                           "write_place", "read_component"}),
}


def _schemas_for_target(target: Optional[Dict], schemas: List[Dict],
                        target_tools: Dict[str, frozenset]) -> List[Dict]:
    allowed = target_tools.get((target or {}).get("check", {}).get("type"))
    if not allowed:
        return schemas
    return [s for s in schemas if s.get("function", {}).get("name") in allowed]


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
    if view and view.get("place_ids"):
        edges = view.get("edges", {})
        counts = view.get("interactable_counts", {})
        unreachable = set(view.get("unreachable", []))
        place_lines = [
            f"  {pid} -> {edges.get(pid, [])}"
            f"  ({'UNREACHABLE' if pid in unreachable else 'reachable'}, "
            f"{counts.get(pid, 0)} interactables)"
            for pid in view["place_ids"]
        ]
        extra = []
        if view.get("items_never_taken"):
            extra.append(f"items never taken: {view['items_never_taken']}")
        if view.get("items_never_used"):
            extra.append(f"items never used: {view['items_never_used']}")
        lines += [
            "",
            "CURRENT PLACES (these already exist — reuse these EXACT ids; `move` ONLY to a place "
            "id listed here or one you also create this step):",
            *place_lines,
            *(["  " + " | ".join(extra)] if extra else []),
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
            "the content above. Call a write/edit tool NOW to make a change.",
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
        # If the agent is spinning on reads, take read tools away so it must act — and turn
        # reasoning on, since a stalled small model rarely breaks the loop with thinking off.
        stalled = context.get("stalled")
        if stalled:
            schemas = [s for s in schemas
                       if not s.get("function", {}).get("name", "").startswith("read")]
        messages = MessageBuilder(system).extend(
            [MessageBuilder.user_msg(_render_context(context))]).build()
        response = (conn.generate_with_tools(messages, schemas, reasoning="high",
                                             max_tokens=_BUILD_MAX_TOKENS)
                    if stalled else conn.generate_with_tools(messages, schemas,
                                                             max_tokens=_BUILD_MAX_TOKENS))
        action = _parse_action(response)
        args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
        target = args.get("node_id") or args.get("room_id") or args.get("component_id") or ""
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


def _create_guard(dispatch: Callable, view_fn: Callable, tool: str,
                  id_key: str, id_list_key: str, noun: str) -> Callable:
    """Wrap dispatch so the count-driving write tool refuses to overwrite an existing item.
    Driving `count` the job is to ADD; a small model loves to rewrite item #1 instead, which
    never raises the count — so reject it and steer to a fresh id. count-only (other targets
    legitimately rewrite)."""
    existing = set((view_fn() or {}).get(id_list_key) or [])

    def guarded(action: Dict) -> Dict:
        if action.get("tool") == tool:
            iid = (action.get("args") or {}).get(id_key)
            if iid in existing:
                return {"ok": False, "error":
                        f"{noun} {iid!r} already exists — to raise the COUNT write a NEW {noun} "
                        f"id; do not rewrite an existing one."}
            result = dispatch(action)
            # Only claim the id once the write actually succeeds — a rejected write (e.g. a thin
            # node hitting the line floor) must stay retryable, not get locked out as "exists".
            if iid and isinstance(result, dict) and result.get("ok"):
                existing.add(iid)
            return result
        return dispatch(action)

    return guarded


def _make_subloop(mode: str, prompts: Dict[str, str], target_prompt: Dict[str, str],
                  target_tools: Dict[str, frozenset], count_tool: str, id_key: str,
                  id_list_key: str, noun: str, noun_plural: str,
                  connector=None, component_guide: str = "", cap: int = 20) -> Callable:
    """A stateful sub-agent that drives ONE target check to green, with working memory.

    Unlike the stateless decider (one tool call from rebuilt context), this keeps a tool
    conversation: it reads/writes/edits and SEES its own results, looping until the target
    passes or it hits the step cap. The transcript holds only this task's tool calls (never
    the whole artifact) and MessageBuilder.build() enforces a char budget, so context stays
    bounded — the per-target reset is what keeps it from rotting as the game grows. Shared by
    the node_scripts (VN) and rooms (point-and-click) loops; the maps differ, the loop doesn't.
    """
    from llm_clients.connector_selector import get_connector
    conn = connector or get_connector()
    guide_suffix = f"\n\n{component_guide}" if component_guide else ""
    base_schemas = _schemas_for_mode(mode, TOOL_SCHEMAS)

    def run(target, context, dispatch, target_met, report, budget, view_fn):
        ctx = dict(context)
        ctx["target"] = target
        # Gate tools AND the system prompt to THIS target's job: driving `count` exposes only
        # the write tool with the author prompt; a fix target gets edit tools + the repair prompt.
        schemas = _schemas_for_target(target, base_schemas, target_tools)
        system = _prompt_for_target(target, prompts, target_prompt) + guide_suffix
        if target["check"].get("type") == "count":
            dispatch = _create_guard(dispatch, view_fn, count_tool, id_key, id_list_key, noun)
        mb = MessageBuilder(system).add_user(_render_context(ctx))

        # Reasoning escalation: small local models build fine with thinking OFF, but once a
        # target stops progressing (tool calls erroring, no new node landing) they spiral and
        # never recover on their own. After STALL_LIMIT unproductive iterations, turn reasoning
        # on for the rest of THIS target. Resets each target (run() is called fresh per target).
        # A read is never "progress" — it changes no artifact — so a string of read_node calls
        # (the model sightseeing the graph) counts as a stall and trips the same brake the
        # stateless decider has: escalate reasoning AND drop read tools so it must act.
        # Repair targets (fix kind) start escalated: surgical edits to existing wiring need
        # thinking from the first iteration — with it off the model garbles labels (e.g.
        # repeated-token names) instead of repointing jumps, and only spirals from there.
        STALL_LIMIT = 2
        kind = target_prompt.get(target["check"].get("type"), "author")
        escalated = kind == "fix"
        reads_dropped = False
        stall = 0

        for _ in range(min(cap, max(budget, 0))):
            response = (conn.generate_with_tools(mb.build(), schemas, reasoning="high",
                                                 max_tokens=_BUILD_MAX_TOKENS)
                        if escalated else conn.generate_with_tools(mb.build(), schemas,
                                                                   max_tokens=_BUILD_MAX_TOKENS))
            if "error" in response:
                logger.warning("subloop LLM error: %s", response["error"])
                break
            message = (response.get("choices") or [{}])[0].get("message", {})
            tool_calls = [tc for tc in (message.get("tool_calls") or [])
                          if tc.get("function", {}).get("name")]
            if not tool_calls:
                # The model answered with prose instead of a tool call — a small-model failure
                # mode (it dumps the whole artifact as text). Don't spin SILENTLY: surface it as
                # a step, nudge it to use a tool, escalate reasoning, and give up loudly after a
                # couple tries rather than letting the executor advance steps with no trace.
                stall += 1
                report("no tool call — model returned text instead; nudging to use a tool")
                if not escalated and stall >= STALL_LIMIT:
                    escalated = True
                    logger.info("subloop: %dx text-not-tool-call on %s — escalating reasoning",
                                stall, target["check"].get("type"))
                if stall > STALL_LIMIT + 1:
                    logger.warning("subloop: model kept returning text not tool calls on %s — "
                                   "giving up this target", target["check"].get("type"))
                    break
                mb.add_user("You MUST respond with a tool call, not prose. Do not write the "
                            "content as text — pick one of the available tools and pass the "
                            "content as its arguments.")
                continue

            mb.add_assistant(message.get("content"), tool_calls=tool_calls)
            made_progress = False
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = _parse_tool_args(tc)
                result = dispatch({"tool": name, "args": args})
                mb.add_tool_result(tc.get("id", ""),
                                   json.dumps(result, ensure_ascii=False)[:800])
                tgt = (args.get("node_id") or args.get("place_id") or args.get("match_id")
                       or args.get("component_id") or "")
                if args.get("interactable_id"):
                    tgt = f"{tgt}/{args['interactable_id']}" if tgt else args["interactable_id"]
                err = result.get("error") if isinstance(result, dict) else None
                if not err and name not in _READ_TOOLS:
                    made_progress = True
                report(f"{name}({tgt}): " + (f"error — {err}" if err else "ok"))

            stall = 0 if made_progress else stall + 1
            if stall >= STALL_LIMIT:
                if not escalated:
                    escalated = True
                    logger.info("subloop stalled %dx on %s — escalating reasoning to high",
                                stall, target["check"].get("type"))
                if not reads_dropped:
                    reads_dropped = True
                    schemas = [s for s in schemas
                               if s.get("function", {}).get("name") not in _READ_TOOLS]
                    logger.info("subloop stalled %dx on %s — dropping read tools to force action",
                                stall, target["check"].get("type"))

            # The artifact changed; re-show the graph so the agent tracks ids as it builds.
            view = view_fn()
            ids = (view or {}).get(id_list_key)
            if ids:
                note = f"CURRENT {noun_plural}: " + json.dumps(ids, ensure_ascii=False)
                if view.get("unreachable"):
                    note += f" | UNREACHABLE: {view['unreachable']}"
                mb.add_user(note)

            if target_met():
                break

    return run


def make_node_subloop(connector=None, component_guide: str = "", cap: int = 20) -> Callable:
    return _make_subloop(
        "nodes", _NODE_PROMPTS, _TARGET_PROMPT, _TARGET_TOOLS,
        count_tool="write_node", id_key="node_id", id_list_key="node_ids",
        noun="node", noun_plural="NODES",
        connector=connector, component_guide=component_guide, cap=cap)


def make_place_subloop(connector=None, component_guide: str = "", cap: int = 20) -> Callable:
    return _make_subloop(
        "places", _PLACE_PROMPTS, _PLACE_TARGET_PROMPT, _PLACE_TARGET_TOOLS,
        count_tool="write_place", id_key="place_id", id_list_key="place_ids",
        noun="place", noun_plural="PLACES",
        connector=connector, component_guide=component_guide, cap=cap)
