"""Services — the bounded gateway a module's Fix calls through.

A module's `get_fix` returns a callable `Fix(services) -> None`; the loop builds a fresh `Services`
per fix and invokes it. The module owns the SHAPE of the fix (one call or an iterative author loop);
`Services` owns the LIMITS — every LLM call runs the pause/cancel checkpoint and consumes a per-fix
step budget. When the budget hits zero `infer` raises `BudgetExhausted`, a BaseException a fix cannot
catch, so even a naive `while True:` fix unwinds back to the loop. Modules never see the connector.

`author_loop` is the shared iterative Fix the content modules return for their count-driven target:
author ONE item per call behind the slot guard, with working memory, until the target clears (or the
budget runs out).
"""

import json
import logging
from typing import Dict, Optional

from maestro import context_render as cr
from maestro.modules.context import build_context, render_dict
from maestro.modules.module import load_prompt, skeleton_guide
from maestro.run_control import BuildCancelled

logger = logging.getLogger(__name__)

_READ_TOOLS = {"read_node", "read_place", "read_component", "read_story_state"}
_BUILD_MAX_TOKENS = 8000
_STALL_LIMIT = 2


class BudgetExhausted(BaseException):
    """A fix used its whole step budget. BaseException so a fix can't swallow it — it unwinds to the
    loop, which moves on to the next error."""


# ── tool-call plumbing ───────────────────────────────────────────────────────
def _tool_schemas():
    from maestro.tools import TOOL_SCHEMAS
    return TOOL_SCHEMAS


def filter_schemas(allowed, schemas=None):
    schemas = schemas if schemas is not None else _tool_schemas()
    if not allowed:
        return schemas
    allowed = set(allowed)
    return [s for s in schemas if s.get("function", {}).get("name") in allowed]


def parse_args(raw: str) -> Dict:
    raw = (raw or "{}").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
        raw = raw.strip()
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("unparseable tool args: %r", raw)
        return {}


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


def parse_action(response: Dict, schemas=None) -> Dict:
    if "error" in response:
        logger.warning("decider LLM error: %s", response["error"])
        return {}
    message = (response.get("choices") or [{}])[0].get("message", {})
    tcs = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
    if not tcs:
        salvaged = salvage_tool_call(message.get("content"), schemas)
        if not salvaged:
            return {}
        tcs = [salvaged]
    fn = tcs[0]["function"]
    return {"tool": fn["name"], "args": parse_args(fn.get("arguments"))}


def _summarize(action: Dict, result: Dict) -> str:
    name = action.get("tool", "?")
    args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
    target = (args.get("node_id") or args.get("place_id") or args.get("match_id")
              or args.get("component_id") or "")
    if args.get("interactable_id"):
        target = f"{target}/{args['interactable_id']}" if target else args["interactable_id"]
    label = f"{name}({target})" if target else name
    if isinstance(result, dict) and result.get("error"):
        return f"{label}: error — {result['error']}"
    return f"{label}: ok"


def _read_payload(action: Dict, result: Dict) -> Optional[str]:
    if action.get("tool") not in _READ_TOOLS or not isinstance(result, dict) or result.get("error"):
        return None
    for key in ("content", "story_state"):
        if key in result:
            val = result[key]
            text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            if len(text) > 2000:
                text = text[:2000] + " …(truncated)"
            return f"{_summarize(action, result)}\n{text}"
    return None


def _create_guard(dispatch, view_fn, tool, id_key, id_list_key, noun):
    """Wrap the count-driving write tool so the graph grows by DESIGN: no overwrite, and a new node
    must fill THE ASSIGNED SLOT (the system-picked open slot). Entry + no-open-slots are exempt so it
    can't deadlock. Slot rule fires only for a view that publishes `open_slots` (nodes); places keep
    the no-overwrite rule alone."""
    def guarded(name, args) -> Dict:
        if name != tool:
            return dispatch(name, args)
        view = view_fn() or {}
        existing = set(view.get(id_list_key) or [])
        iid = (args or {}).get(id_key)
        if iid in existing:
            return {"ok": False, "error":
                    f"{noun} {iid!r} already exists — to raise the COUNT write a NEW {noun} id; do "
                    f"not rewrite an existing one."}
        chosen = cr.pick_slot(view) if view.get("open_slots") is not None else None
        if view.get("open_slots") is not None and existing and chosen is not None \
                and iid != chosen["id"]:
            return {"ok": False, "error":
                    f"{noun} {iid!r} is not the assigned slot — write {chosen['id']!r} next (the "
                    f"scene the story leads into). Use that EXACT id as the {noun} id."}
        if id_key == "node_id":
            beat = cr.beat_for_new_node(view, chosen, bool(existing))
            if beat:
                args = {**(args or {}), "beat": beat}
        return dispatch(name, args)
    return guarded


# ── Services: the bounded gateway ────────────────────────────────────────────
class Services:
    def __init__(self, conn, tools, spec: Dict, state, *, budget: int, control=None,
                 on_event=None, report=None, escalate: bool = False):
        self.conn = conn
        self.tools = tools
        self.spec = spec
        self.state = state
        self.budget = budget
        self.control = control
        self.on_event = on_event
        self.report = report         # (summary) -> None : the loop's per-action progress sink
        self.escalate = escalate     # the loop saw a cross-fix stall — start hot
        self.spent = 0
        self.last_result: Optional[str] = None
        self.last_read: Optional[str] = None

    def _report(self, summary: str) -> None:
        self.last_result = summary
        if self.report is not None:
            self.report(summary)

    def _emit(self, event_type: str, **fields) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event({"type": event_type, **fields})
        except Exception:
            logger.exception("on_event callback failed for %s", event_type)

    def checkpoint(self) -> None:
        c = self.control
        if c is None:
            return
        if c.cancelled:
            raise BuildCancelled()
        if c.paused:
            c.set_status("paused")
            self._emit("build_paused", step=self.spent)
            c.wait_while_paused()
            if c.cancelled:
                raise BuildCancelled()
            c.set_status("running")
            self._emit("build_resumed", step=self.spent)

    def infer(self, messages, schemas, reasoning: Optional[str] = None, max_tokens: Optional[int] = None):
        """One LLM call — checkpointed, budgeted. Raises BudgetExhausted when the per-fix cap is hit."""
        self.checkpoint()
        if self.spent >= self.budget:
            raise BudgetExhausted()
        self.spent += 1
        kw = {"max_tokens": max_tokens or _BUILD_MAX_TOKENS}
        eff = reasoning or ("high" if self.escalate else None)
        if eff:
            kw["reasoning"] = eff
        return self.conn.generate_with_tools(messages, schemas, **kw)

    def dispatch(self, name, args) -> Dict:
        fn = self.tools.get(name)
        if fn is None:
            return {"error": f"unknown tool: {name!r}"}
        try:
            return fn(**(args or {}))
        except Exception as e:
            logger.exception("tool %s failed", name)
            return {"error": f"{name} failed: {e}"}

    def run(self, prompt) -> None:
        """A stateless single step: infer one tool call from `prompt` and dispatch it."""
        from llm_clients.message_builder import MessageBuilder
        schemas = filter_schemas(prompt.allowed_tools)
        if self.escalate:
            schemas = [s for s in schemas if not s.get("function", {}).get("name", "").startswith("read")]
        msgs = MessageBuilder(prompt.system).add_user(prompt.user).build()
        action = parse_action(self.infer(msgs, schemas, max_tokens=prompt.max_tokens), schemas)
        if not action.get("tool"):
            self._report("no tool call — model returned prose")
            return
        result = self.dispatch(action["tool"], action.get("args", {}))
        self.last_read = _read_payload(action, result)
        self._report(_summarize(action, result))


# ── author_loop: the shared iterative Fix ────────────────────────────────────
def author_loop(context, error, services: Services, *, module, guard: Dict) -> None:
    """Drive a count-target to green: ONE guarded item per call, with working memory, until the
    target's error clears. Bounded by `services` (infer raises BudgetExhausted at the cap)."""
    from llm_clients.message_builder import MessageBuilder

    job = module.job_for(error.code)
    system = load_prompt(module.prompts.get(job, module.mode_prompt))
    if module.skeleton:
        system += "\n\n" + skeleton_guide(module.component, module.skeleton)
    schemas = filter_schemas(module.tools_for(error.code))
    view_fn = lambda: module.view(services.state.load_artifact())
    dispatch = _create_guard(services.dispatch, view_fn, guard["count_tool"], guard["id_key"],
                             guard["id_list_key"], guard["noun"])

    rd = render_dict(build_context(services.spec, services.state, errors=[error]),
                     active=module.component, target=error, active_view=view_fn(),
                     available_tools=module.tools_for(error.code))
    mb = MessageBuilder(system).add_user(module.render_context(rd))

    def target_met() -> bool:
        c = build_context(services.spec, services.state)
        return not any(e.identity() == error.identity() for e in module.get_errors(c))

    escalated = job == "fix"
    reads_dropped = False
    stall = 0
    while True:   # bounded by services.budget — infer() raises BudgetExhausted at the cap
        response = services.infer(mb.build(), schemas, reasoning="high" if escalated else None)
        if "error" in response:
            logger.warning("author_loop LLM error: %s", response["error"])
            return
        message = (response.get("choices") or [{}])[0].get("message", {})
        tcs = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
        if not tcs:
            salvaged = salvage_tool_call(message.get("content"), schemas)
            if salvaged:
                services._report("salvaged tool call from text response")
                tcs = [salvaged]
                message = {**message, "content": None}
            else:
                stall += 1
                services._report("no tool call — model returned text; nudging")
                if not escalated and stall >= _STALL_LIMIT:
                    escalated = True
                if stall > _STALL_LIMIT + 1:
                    return
                mb.add_user("You MUST respond with a tool call, not prose. Pick one of the available "
                            "tools and pass the content as its arguments.")
                continue

        mb.add_assistant(message.get("content"), tool_calls=tcs)
        made_progress = False
        for tc in tcs:
            name = tc["function"]["name"]
            args = parse_args(tc.get("function", {}).get("arguments"))
            result = dispatch(name, args)
            mb.add_tool_result(tc.get("id", ""), json.dumps(result, ensure_ascii=False)[:800])
            err = result.get("error") if isinstance(result, dict) else None
            if not err and name not in _READ_TOOLS:
                made_progress = True
            services._report(_summarize({"tool": name, "args": args}, result))

        stall = 0 if made_progress else stall + 1
        if stall >= _STALL_LIMIT:
            escalated = True
            if not reads_dropped:
                reads_dropped = True
                schemas = [s for s in schemas if s.get("function", {}).get("name") not in _READ_TOOLS]

        note = module.render_progress(view_fn() or {})
        if note:
            mb.add_user(note)
        if target_met():
            return
