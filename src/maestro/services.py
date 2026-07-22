"""Services — the bounded gateway a module's Fix calls through.

A module's `get_fix` returns a callable `Fix(services) -> None`; the loop builds a fresh `Services`
per fix and invokes it. The module owns the SHAPE of the fix (one call or an iterative author loop);
`Services` owns the LIMITS — every LLM call runs the pause checkpoint and consumes a per-fix
step budget. When the budget hits zero `infer` raises `BudgetExhausted`, a BaseException a fix cannot
catch, so even a naive `while True:` fix unwinds back to the loop. Modules never see the connector.

Count-driven targets don't need a bespoke loop: `get_errors` fans a shortfall into one per-slot
create-error each, so the outer loop authors one item per step (the slot guard, installed on the
step's dispatch by `Module._single_fix`, keeps each write additive + in order).
"""

import json
import logging
from typing import Dict, Optional

from llm_clients.message_builder import MessageBuilder

logger = logging.getLogger(__name__)

_READ_TOOLS = {"read_file"}
_BUILD_MAX_TOKENS = 8000


class BudgetExhausted(BaseException):
    """A fix used its whole step budget. BaseException so a fix can't swallow it — it unwinds to the
    loop, which moves on to the next error."""


# ── tool-call plumbing ───────────────────────────────────────────────────────
def _tool_schemas():
    # No global tool-schema registry in the codegen world — each fix scopes its own tools (the
    # codegen fix uses a raw completion + a single write tool), so the default offer is empty.
    return []


def filter_schemas(allowed, schemas=None):
    schemas = schemas if schemas is not None else _tool_schemas()
    if not allowed:
        return schemas
    allowed = set(allowed)
    return [s for s in schemas if s.get("function", {}).get("name") in allowed]


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
    for key in ("content",):
        if key in result:
            val = result[key]
            text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            if len(text) > 2000:
                text = text[:2000] + " …(truncated)"
            return f"{_summarize(action, result)}\n{text}"
    return None


def _create_guard(dispatch, view_fn, tool, id_key, id_list_key, noun, assigned=None, prepare=None):
    """Wrap the count-driving write tool so the graph grows by DESIGN: no overwrite, and a new node
    must fill THE ASSIGNED SLOT. `assigned` is the slot dict picked at prompt-build time — the SAME
    snapshot the prompt rendered, so prompt and guard agree by construction (a sibling parallel fix
    landing in between shifts live slot indices, so the guard must not re-pick from the live view).
    `prepare` is the owning module's arg-finisher (scenes code-fills the node id + the
    (storyline, beat) stamp) — this guard knows no module's policy. Entry + no-open-slots are
    exempt so it can't deadlock."""
    def guarded(name, args) -> Dict:
        if name != tool:
            return dispatch(name, args)
        view = view_fn() or {}
        if prepare is not None:
            # prepare runs FIRST: the owning module may code-fill the write's identity (scenes
            # forces the assigned slot's node id), so the checks below validate what will
            # actually be written, never a model-picked id that prepare would discard.
            args = prepare(view, assigned, args or {})
        existing = set(view.get(id_list_key) or [])
        iid = (args or {}).get(id_key)
        if iid in existing:
            return {"ok": False, "error":
                    f"{noun} {iid!r} already exists — to raise the COUNT write a NEW {noun} id; do "
                    f"not rewrite an existing one."}
        if assigned is not None and existing and iid != assigned["id"]:
            return {"ok": False, "error":
                    f"{noun} {iid!r} is not the assigned slot — write {assigned['id']!r} next (the "
                    f"scene the story leads into). Use that EXACT id as the {noun} id."}
        return dispatch(name, args)
    return guarded


# ── Services: the bounded gateway ────────────────────────────────────────────
class Services:
    def __init__(self, conn, tools, spec: Dict, state, *, budget: int, control=None,
                 on_event=None, report=None, escalate: bool = False, lock=None):
        self.conn = conn
        self.tools = tools
        self.spec = spec
        self.state = state
        self.budget = budget
        self.control = control
        self.on_event = on_event
        self.report = report         # (summary) -> None : the loop's per-action progress sink
        self.escalate = escalate     # the loop saw a cross-fix stall — start hot
        self.lock = lock             # serializes tool dispatch across parallel fixes (LLM calls don't)
        self.spent = 0
        self.last_result: Optional[str] = None
        self.last_read: Optional[str] = None
        self.allowed: Optional[frozenset] = None   # tools in scope for the current step (None = all)

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
        if c.paused:
            c.set_status("paused")
            self._emit("build_paused", step=self.spent)
            c.wait_while_paused()
            c.set_status("running")
            self._emit("build_resumed", step=self.spent)

    def infer(self, messages, schemas, reasoning: Optional[str] = None,
              max_tokens: Optional[int] = None):
        """One LLM call — checkpointed, budgeted. Raises BudgetExhausted when the per-fix cap is
        hit."""
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
        # Scope enforcement — the single gate for every fix shape. A small model learns other tool
        # names from the prompt prose and calls an off-phase tool (e.g. write_combatant while the
        # step authors an ability); dispatch refuses it rather than thrashing on it. Same source as
        # the offered schemas, so offer and enforcement can't drift.
        if self.allowed is not None and name not in self.allowed:
            return {"ok": False, "error":
                    f"{name!r} is not available for this step — call one of {sorted(self.allowed)}"}
        fn = self.tools.get(name)
        if fn is None:
            return {"error": f"unknown tool: {name!r}"}
        try:
            return fn(**(args or {}))
        except Exception as e:
            logger.exception("tool %s failed", name)
            return {"error": f"{name} failed: {e}"}

    def run(self, prompt, *, dispatch=None) -> None:
        """A stateless single step: infer one tool call from `prompt` and dispatch it. `dispatch`
        overrides self.dispatch — a create fix installs its slot guard this way."""
        dispatch = dispatch or self.dispatch
        allowed = frozenset(prompt.allowed_tools) or None
        if self.escalate and allowed:
            # A stalled fix must ACT, not re-read; drop reads from the offer AND the enforcement
            # (same source, so they can't drift).
            allowed = frozenset(t for t in allowed if not t.startswith("read")) or allowed
        self.allowed = allowed   # what dispatch enforces this step
        schemas = filter_schemas(allowed)
        msgs = MessageBuilder(prompt.system).add_user(prompt.user).build()
        action = parse_action(self.infer(msgs, schemas, max_tokens=prompt.max_tokens), schemas)
        if not action.get("tool"):
            # The common cause is an EMPTY response: the model burned the whole max_tokens budget
            # on reasoning and was truncated before emitting the call. One retry with reasoning
            # forced off converts a wasted step into the intended tool call.
            self._report("no tool call (likely reasoning overran max_tokens) — retrying with "
                         "reasoning off")
            action = parse_action(self.infer(msgs, schemas, reasoning="none",
                                             max_tokens=prompt.max_tokens), schemas)
        if not action.get("tool"):
            self._report("no tool call — model returned prose")
            return
        # The lock covers the whole guarded dispatch (view read + validate + write), so parallel
        # fixes' read-modify-write cycles on the artifact can't interleave.
        if self.lock is not None:
            with self.lock:
                result = dispatch(action["tool"], action.get("args", {}))
        else:
            result = dispatch(action["tool"], action.get("args", {}))
        self.last_read = _read_payload(action, result)
        self._report(_summarize(action, result))

