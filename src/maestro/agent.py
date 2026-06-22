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

# Per-stage gating (which tools + which system prompt a mode/target gets) now lives on the owning
# Module (maestro.discrete) and arrives via the composed bundle, so adding a module no longer
# edits this file. The decider gets {mode -> tools} and {mode -> prompt-file} maps; the sub-loop
# reads its module's prompts/target_jobs/target_tools/subloop config directly.

_PROMPT_CACHE: Dict[str, str] = {}


def _load_prompt(name: str) -> str:
    cached = _PROMPT_CACHE.get(name)
    if cached is None:
        cached = render_template(_PROMPTS_DIR / name, {})
        _PROMPT_CACHE[name] = cached
    return cached


def _filter_schemas(allowed, schemas: List[Dict]) -> List[Dict]:
    """Keep only schemas whose tool name is in `allowed` (None/empty → keep all)."""
    if not allowed:
        return schemas
    return [s for s in schemas if s.get("function", {}).get("name") in allowed]


def _prompt_for_target(target: Optional[Dict], prompts: Dict[str, str],
                       target_jobs: Dict[str, str]) -> str:
    # A target's job (author fresh content vs. fix existing wiring) picks the system prompt.
    # Unlisted check types default to author.
    job = target_jobs.get((target or {}).get("check", {}).get("type"), "author")
    return prompts[job]


def _schemas_for_target(target: Optional[Dict], schemas: List[Dict],
                        target_tools: Dict[str, frozenset]) -> List[Dict]:
    allowed = target_tools.get((target or {}).get("check", {}).get("type"))
    return _filter_schemas(allowed, schemas)


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
                     component_guide: str = "", mode_tools: Optional[Dict[str, frozenset]] = None,
                     mode_prompts: Optional[Dict[str, str]] = None) -> Callable:
    from llm_clients.connector_selector import get_connector
    conn = connector or get_connector()
    all_schemas = tool_schemas or TOOL_SCHEMAS
    guide_suffix = f"\n\n{component_guide}" if component_guide else ""
    mode_tools = mode_tools or {}      # component-mode -> allowed tool names
    mode_prompts = mode_prompts or {}  # component-mode -> system-prompt filename

    def decide(context: Dict) -> Dict:
        mode = context.get("mode")
        prompt_name = mode_prompts.get(mode)
        system = (_load_prompt(prompt_name) if prompt_name else _SYSTEM) + guide_suffix
        schemas = _filter_schemas(mode_tools.get(mode), all_schemas)
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


def rewrite_node(spec, state, node_id: str, note: str, tools: Dict[str, Callable],
                 connector=None, component_guide: str = "", report: Optional[Callable] = None,
                 cap: int = 4) -> Dict:
    """Regenerate ONE node, steered by a human note ("make Mara colder"). Uses the dialogue
    author prompt + the build's write_node (force, so a locked nodes component is overwritten).
    A focused mini-loop: one node, a couple of retries if the model fumbles the tool call.
    Returns {ok, node_id} or {ok: False, error}."""
    from llm_clients.connector_selector import get_connector
    conn = connector or get_connector()
    ns = state.read_component("nodes") or {}
    existing = (ns.get("nodes") or {}).get(node_id)
    if existing is None:
        return {"ok": False, "error": f"no node {node_id!r} to rewrite"}

    say = report or (lambda _msg: None)
    system = _load_prompt("write_node.txt") + (f"\n\n{component_guide}" if component_guide else "")
    schemas = _filter_schemas({"write_node"}, TOOL_SCHEMAS)
    upstream = {cid: state.read_component(cid)
                for cid in (c.get("id") for c in spec.components) if cid != "nodes"}
    upstream = {k: v for k, v in upstream.items() if v is not None}

    task = "\n".join([
        f"Rewrite the dialogue node '{node_id}'. Keep this exact node id.",
        f"Unless the direction says otherwise, keep its `end` ({json.dumps(existing.get('end', {}), ensure_ascii=False)}) "
        f"so it stays wired into the graph.",
        "",
        f"HUMAN DIRECTION (the change to make): {note}",
        "",
        f"CURRENT NODE:\n{json.dumps(existing, ensure_ascii=False)}",
        "",
        "LOCKED UPSTREAM (use these EXACT character/entity ids):",
        json.dumps(upstream, ensure_ascii=False),
        f"STORY STATE: {json.dumps(state.read_story_state() or {}, ensure_ascii=False)}",
        "",
        f"Call write_node with node_id='{node_id}' and the full rewritten content. Tool call only, not prose.",
    ])
    mb = MessageBuilder(system).add_user(task)

    for _ in range(cap):
        response = conn.generate_with_tools(mb.build(), schemas, reasoning="high",
                                            max_tokens=_BUILD_MAX_TOKENS)
        action = _parse_action(response)
        if action.get("tool") != "write_node":
            say("no write_node tool call — nudging")
            mb.add_user("Respond with a write_node TOOL CALL (not prose), passing the rewritten "
                        f"content for node_id='{node_id}'.")
            continue
        args = {**(action.get("args") or {}), "node_id": node_id, "force": True}
        result = tools["write_node"](**args)
        if result.get("ok"):
            say(f"rewrote {node_id}")
            return {"ok": True, "node_id": node_id}
        say(f"write_node rejected: {result.get('error')}")
        mb.add_user(f"That was rejected: {result.get('error')}. Fix it and call write_node again.")
    return {"ok": False, "error": f"could not rewrite {node_id!r} after {cap} attempts"}


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


def make_subloop(module, connector=None, component_guide: str = "", cap: int = 20) -> Callable:
    """A stateful sub-agent that drives ONE target check to green, with working memory.

    Unlike the stateless decider (one tool call from rebuilt context), this keeps a tool
    conversation: it reads/writes/edits and SEES its own results, looping until the target
    passes or it hits the step cap. The transcript holds only this task's tool calls (never
    the whole artifact) and MessageBuilder.build() enforces a char budget, so context stays
    bounded — the per-target reset is what keeps it from rotting as the game grows. One loop,
    fully parameterized by the owning Module's config (nodes / places / any future content
    module); the maps differ, the loop doesn't.
    """
    from llm_clients.connector_selector import get_connector
    conn = connector or get_connector()
    guide_suffix = f"\n\n{component_guide}" if component_guide else ""
    cfg = module.subloop
    count_tool, id_key, id_list_key = cfg["count_tool"], cfg["id_key"], cfg["id_list_key"]
    noun, noun_plural = cfg["noun"], cfg["noun_plural"]
    prompts = {job: _load_prompt(fn) for job, fn in module.prompts.items()}
    target_jobs = module.target_jobs
    target_tools = module.target_tools
    base_schemas = _filter_schemas(module.mode_tools, TOOL_SCHEMAS)

    def run(target, context, dispatch, target_met, report, budget, view_fn):
        ctx = dict(context)
        ctx["target"] = target
        # Gate tools AND the system prompt to THIS target's job: driving `count` exposes only
        # the write tool with the author prompt; a fix target gets edit tools + the repair prompt.
        schemas = _schemas_for_target(target, base_schemas, target_tools)
        system = _prompt_for_target(target, prompts, target_jobs) + guide_suffix
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
        kind = target_jobs.get(target["check"].get("type"), "author")
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
