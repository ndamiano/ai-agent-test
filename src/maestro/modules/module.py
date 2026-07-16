"""Module — the composable unit a game is built from.

A module ensures a set of context is valid, and helps fix invalid context, by reporting errors and
how to fix them.

A module IS a list of `Check`s — each a (detector -> fix) pair over the shared components. The base
runs them: `get_errors` sweeps the checks; `get_correction_prompt`/`get_fix` build the fix for an
emitted error from the check it came from (indexed by `Error.code`). A subclass declares `checks`
and the authoring attrs; it overrides no method.
"""

from __future__ import annotations

import functools
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

class ErrorType(Enum):
    HUMAN = "human"
    BUILD = "build"
    FIX = "fix"


@dataclass(frozen=True)
class Error:
    type: ErrorType
    code: str                 # check type, e.g. "min_count" / "dangling_ref" — the identity axis
    component: str            # the on-disk component the failure lands in
    message: str              # human-facing description; reword-safe, outside identity()
    path: Optional[str] = None      # locator within the component (id / json path)
    ref: Optional[str] = None       # for reference errors: the unresolved id
    kind: Optional[str] = None      # for reference errors: what KIND of id failed to resolve
                                    # (character/node/item/...) — lets a fix build a per-kind prompt

    def identity(self) -> Tuple:
        """Stable key the loop compares across steps for stall detection, across rewordings of
        `message`. All-string so tuples are totally orderable (None path/ref would TypeError
        against a str one in `prioritize`)."""
        return (self.type.value, self.code, self.component, self.path or "", self.ref or "")


def idkey(error: "Error") -> str:
    """A JSON-serializable form of `Error.identity()` — the durable key a human waiver is stored
    and matched under (survives process restarts and message rewordings)."""
    import json
    return json.dumps([error.type.value, error.code, error.component, error.path, error.ref],
                      ensure_ascii=False)


# ── The fix instruction a module hands the loop for one error ─────────────────
@dataclass(frozen=True)
class CorrectionPrompt:
    system: str                       # load-bearing system prompt (from a .txt, never inlined)
    user: str                         # the per-step user message (context + the target error)
    allowed_tools: Tuple[str, ...]    # the tools this fix may call
    max_tokens: Optional[int] = None  # per-target output ceiling; None = the build default


# ── The (detector -> fix) pair a module is a list of ──────────────────────────
@dataclass(frozen=True)
class Check:
    """One check: a detector plus how to fix what it detects.

    `detect(check, module, context) -> [Error]` reports 0..N errors (a count shortfall fans into N
    via `checks.slot_errors`; a clean check returns []). The remaining fields declare the FIX for an
    error this check emits (the loop finds the check by `Error.code`): the prompt/tools/skeleton the
    correction step runs with (each falls back to the module default when None), an optional slot
    `guard` for a create tool, or a `build_prompt` that assembles the whole CorrectionPrompt bespoke
    (state/human). `prompt`/`skeleton` may be a `callable(context)->str` for a style-dependent step.

    Sweep order is the declared list order. `blocking` = if this check emits, stop and suppress every
    later check (a hard dependency tier — combat's stats before abilities). `when_clean` = skip this
    check unless nothing has been emitted yet (a terminal check — crossref/compile is only meaningful
    once the cheaper checks pass)."""
    code: str
    detect: Callable                       # (check, module, context) -> [Error]
    job: str = "author"                    # author => BUILD tier, else FIX (unless `tier` is set)
    tier: Optional[ErrorType] = None       # error type; defaults from `job`
    blocking: bool = False
    when_clean: bool = False
    prompt: object = None                  # str | callable(ctx)->str | None (=> module.mode_prompt)
    tools: Optional[frozenset] = None      # None => module.mode_tools
    skeleton: object = None                # str | callable(ctx)->str | None (=> module.skeleton)
    guard: Optional[Dict] = None           # slot guard for a create tool (count targets)
    max_tokens: Optional[int] = None
    context: Optional[Callable] = None     # (module, rd) -> str: the per-error user payload;
                                           # None => module.render_context. A repair check points
                                           # at a lean archetype (ctx_structural/ctx_crossref) so
                                           # the fix ships ONLY what that check needs, no dumps.
    build_prompt: Optional[Callable] = None   # (module, ctx, error) -> CorrectionPrompt
    run: Optional[Callable] = None         # (module, ctx, error, slot, services, dispatch) -> None:
                                           # the whole fix body (multi-call subloop); replaces the
                                           # single prompt+dispatch step, still budget-bounded

    def __post_init__(self):
        if self.tier is None:
            object.__setattr__(self, "tier",
                               ErrorType.BUILD if self.job == "author" else ErrorType.FIX)


class Module(ABC):
    """One buildable unit. Sub classes are only required to implement `get_errors`; everything else
     has a working default.
    """

    id: str
    priority: int = 100   # order within an error tier; lower acts first (cast < dialogue)

    # ── authoring surface (defaults are inert) ───────────────────────────────
    component: str = ""                  # the on-disk component this module authors (if any)
    mode_prompt: str = ""                # the system prompt for a single correction step
    mode_tools: frozenset = frozenset()  # the tools a correction step may call (fallback)
    skeleton: str = ""                   # the component's authoring shape, appended to the prompt

    projector: Optional[Callable] = None     # (artifact) -> the compact graph view for this component
    checks: List[Check] = []                 # the (detector -> fix) pairs this module IS (see get_errors)

    def get_errors(self, context) -> List[Error]:
        """Sweep the module's checks in declared order, accumulating their errors. A `blocking` check
        that emits stops the sweep (its tier is a hard dependency for everything below); a
        `when_clean` check is skipped once anything has been emitted (a terminal check). This makes
        the detector=fixer contract literal: a module reports only what its checks can fix."""
        errs: List[Error] = []
        for chk in self.checks:
            if chk.when_clean and errs:
                continue
            got = chk.detect(chk, self, context)
            errs += got
            if chk.blocking and got:
                break
        return errs

    def wrap(self, chk: Check, result, **kw) -> List[Error]:
        """A CheckResult `(ok, detail)` -> `[Error]` (empty when ok), stamped with the check's code +
        tier and this module's component — the common one-line detector body."""
        ok, detail = result
        if ok:
            return []
        return [Error(type=chk.tier, code=chk.code, component=self.component,
                      message=detail or chk.code, **kw)]

    def _check_for(self, code: str) -> Optional[Check]:
        return next((c for c in self.checks if c.code == code), None)

    def check_rank(self, code: str) -> int:
        """The declared position of an error's check — the fix-priority axis within this module.
        Declaration order is the author's intent (author before wire before polish); without this
        the loop would order ties alphabetically by code, fixing polish checks into a half-written
        artifact."""
        return next((i for i, c in enumerate(self.checks) if c.code == code), len(self.checks))

    def get_correction_prompt(self, context, error: Error, slot: int = 0) -> CorrectionPrompt:
        """Build the fix step for `error` from the check it came from (found by code): a bespoke
        `build_prompt` if the check declares one (state/human), else the default author/fix step —
        the check's prompt + tools + skeleton (each falling back to the module default), rendered
        with the live graph view. `slot` is this fix's assigned slot index when the loop runs
        several same-code creates in parallel (worker i authors slot i)."""
        chk = self._check_for(error.code)
        if chk and chk.build_prompt:
            return chk.build_prompt(self, context, error)
        from maestro.modules.context import render_dict
        tools = chk.tools if (chk and chk.tools is not None) else self.mode_tools
        prompt = chk.prompt if chk else None
        skel = chk.skeleton if chk else None
        if callable(prompt):
            prompt = prompt(context)
        if callable(skel):
            skel = skel(context)
        if skel is None:
            skel = self.skeleton
        view = self.view(context.artifact)
        rd = render_dict(context, active=error.component or None, target=error, active_view=view,
                         available_tools=tools, slot_index=slot)
        rd["artifact"] = context.artifact   # renderers compose their own blocks from the raw components
        system = prompt or self.mode_prompt or ""
        if skel:
            system += f"\n\n`{self.component}` JSON SHAPE — fill this skeleton (invent the content):\n{skel}"
        user = chk.context(self, rd) if (chk and chk.context) else self.render_context(rd)
        # Parallel siblings each pick the single most obvious id (everyone writes the hero) and
        # collide on the no-overwrite guard; a view with open_slots already differentiates via the
        # assigned slot, everything else gets told its ordinal.
        if chk and chk.guard and slot > 0 and not (view or {}).get("open_slots"):
            noun = chk.guard.get("noun", "item")
            user += (f"\n\nPARALLEL AUTHORING — you are writing {noun} #{slot + 1}; siblings are "
                     f"writing the others RIGHT NOW, and the most obvious {noun} (the protagonist, "
                     f"the starter one) is #1's job. Author a DIFFERENT {noun} with a distinct id — "
                     f"the {slot + 1}-th one the game needs, e.g. an enemy or a variant.")
        return CorrectionPrompt(system=system, user=user,
                                allowed_tools=tuple(sorted(tools)),
                                max_tokens=(chk.max_tokens if chk else None))

    # ── overridable hooks (sensible defaults) ────────────────────────────────
    def render_context(self, ctx: Dict) -> str:
        """The per-step user message for an AUTHORING check. A content module overrides this and
        CRAFTS exactly the upstream its call needs from `ctx['artifact']`; the base default is a
        minimal target line (a module that owns a multi-call fix body via `Check.run` — the codegen
        path — never reaches this)."""
        import json
        target = ctx.get("target")
        head = f"Address this to-do: {target.message}" if getattr(target, "message", None) else \
            "Address the first to-do item."
        view = ctx.get("active_view")
        tail = f"\n\nCurrent state:\n{json.dumps(view, ensure_ascii=False)}" if view else ""
        return head + tail

    def self_digest(self, artifact: Dict) -> List[str]:
        """A COMPACT view of this module's OWN component, for a structural repair on it (dedup a
        field, merge duplicate ids, fix a broken graph edge). The default is the projector's graph
        view if the module has one, else nothing — a small-component module overrides with an
        id-level index. Never the raw component prose."""
        view = self.view(artifact)
        if not view:
            return []
        import json
        return ["", f"CURRENT {self.component or 'component'} (graph view):",
                json.dumps(view, ensure_ascii=False)]

    def affected_components(self) -> Tuple[str, ...]:
        """The components this module touches when present. A soft surface for context injection —
        not a gate. Defaults to the owned `component`."""
        return (self.component,) if self.component else ()

    def view(self, artifact: Dict) -> Optional[Dict]:
        return self.projector(artifact) if self.projector else None

    # ── the fix ──────────────────────────────────────────────────────────────
    def get_fix(self, context, error: Error, slot: int = 0) -> Callable:
        """Return a Fix — a callable `fix(services)` the loop invokes (it builds the Services). One
        correction step; a count/create error whose check declares a slot `guard` gets its write tool
        wrapped so the step can only ADD the next owed item (no overwrite, right slot). `slot` is the
        assigned slot index for a parallel batch of same-code creates."""
        return functools.partial(self._single_fix, context, error, slot)

    def _single_fix(self, context, error: Error, slot: int, services) -> None:
        """One correction step: build the prompt for this error, let services run it (one LLM call +
        dispatch). Bounded by the services budget like any fix."""
        chk = self._check_for(error.code)
        guard = chk.guard if chk else None
        dispatch = None
        if guard:
            from maestro.services import _create_guard
            # The guard's slot policy is the MODULE's: `assign` picks this worker's slot from the
            # prompt-time view, `prepare` finishes the write args (e.g. scenes stamps the beat).
            assign = guard.get("assign")
            assigned = assign(self.view(context.artifact) or {}, slot) if assign else None
            view_fn = lambda: self.view(services.state.load_artifact())
            dispatch = _create_guard(services.dispatch, view_fn, guard["count_tool"],
                                     guard["id_key"], guard["id_list_key"], guard["noun"],
                                     assigned=assigned, prepare=guard.get("prepare"))
        if chk and chk.run is not None:
            chk.run(self, context, error, slot, services, dispatch or services.dispatch)
            return
        services.run(self.get_correction_prompt(context, error, slot=slot), dispatch=dispatch)

