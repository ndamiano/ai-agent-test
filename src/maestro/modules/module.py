"""Module — the composable unit a game is built from.

Modules depict a set of functionality that one of the projectors can build. They ensure that a
set of context is valid, as well as help fix invalid context. They do this by reporting errors
as well as how to fix those errors.

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
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# TODO:: Should this be in here or should we move this to an appropriate utility file?
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PROMPT_CACHE: Dict[str, str] = {}

def load_prompt(name: str) -> str:
    """A cached prompt-file load."""
    if name not in _PROMPT_CACHE:
        from renpy.templating import render_template
        _PROMPT_CACHE[name] = render_template(_PROMPTS_DIR / name, {})
    return _PROMPT_CACHE[name]


def skeleton_guide(component: str, skeleton: str) -> str:
    return f"`{component}` JSON SHAPE — fill this skeleton (invent the content):\n{skeleton}"


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
    build_prompt: Optional[Callable] = None   # (module, ctx, error) -> CorrectionPrompt
    run: Optional[Callable] = None         # (module, ctx, error, slot, services, dispatch) -> None:
                                           # the whole fix body (multi-call subloop); replaces the
                                           # single prompt+dispatch step, still budget-bounded

    def __post_init__(self):
        if self.tier is None:
            object.__setattr__(self, "tier",
                               ErrorType.BUILD if self.job == "author" else ErrorType.FIX)


class Module(ABC):
    """One mechanic-module. Sub classes are only required to implement `get_errors`; everything else
     has a working default.
    """

    id: str
    substrates: Tuple[str, ...] = ("discrete",)
    priority: int = 100   # order within an error tier; lower acts first (cast < dialogue)

    # ── spec-composition surface (what the proposer picks from) ──────────────
    description: str = ""               # one-line, LLM-facing: what this mechanic adds
    selectable: bool = True             # False = always-on foundation, hidden from the catalog
    requires: Tuple[str, ...] = ()      # modules pulled in automatically when this is chosen

    # ── authoring surface (defaults are inert) ───────────────────────────────
    component: str = ""                  # the on-disk component this module authors (if any)
    mode_prompt: str = ""                # the system prompt for a single correction step
    mode_tools: frozenset = frozenset()  # the tools a correction step may call (fallback)
    skeleton: str = ""                   # the component's authoring shape, appended to the prompt
    schemas: Dict[str, Callable] = {}    # component_id -> structural write-time validator
    skeletons: Dict[str, str] = {}       # component_id -> authoring shape (for the guide)
    tool_names: Tuple[str, ...] = ()     # gated tool-schema names this module contributes
    projected: bool = False              # needs an engine-specific renderer (see unprojectable)

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
        system = load_prompt(prompt or self.mode_prompt)
        if skel:
            system += "\n\n" + skeleton_guide(self.component, skel)
        user = self.render_context(rd)
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
        """The per-step user message. There is no generic component dump — every module is
        expected to override this and CRAFT the context its call needs from `ctx['artifact']`
        (the cr.*_block helpers are the shared formats). This default carries only the
        run-state frame."""
        from maestro import context_render as cr
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx) + cr.scratchpad_block(ctx)
        lines += cr.story_state_block(ctx) + cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)

    def params(self) -> Dict:
        """Tunable knobs -> FLOOR (int knobs take the max when composed, list knobs the union). The
        spec stores the resolved value; get_errors reads it from `context.param(...)`."""
        return {}

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


# ── Registry ──────────────────────────────────────────────────────────────────
MODULE_REGISTRY: Dict[str, Module] = {}


def register_module(m: Module) -> None:
    MODULE_REGISTRY[m.id] = m


def compose(module_ids: Tuple[str, ...]) -> List[Module]:
    """Resolve ids -> live module instances, validating each is known. The human module is always
    included (no composition can opt out of the human in the loop). Order is informational — the
    loop sorts by error type + priority."""
    ids = ("human",) + tuple(mid for mid in module_ids if mid != "human")
    out: List[Module] = []
    for mid in ids:
        m = MODULE_REGISTRY.get(mid)
        if m is None:
            raise KeyError(f"unknown module {mid!r} (registered: {sorted(MODULE_REGISTRY)})")
        out.append(m)
    return out


# ── Spec composition — the proposer picks modules from the catalog ──────────────
# There is no genre/preset box, and no ownership/terminal/exclusion: the proposer is shown the
# selectable modules and chooses any subset. Code force-includes the always-on foundation, pulls
# each pick's `requires` deps, and derives the engine from what can project the resulting set. A set
# is buildable if it has a realization module (one that produces a compile entry) and an engine.
_DEFAULT_MODULES: Tuple[str, ...] = ("cast", "story", "scenes")  # the safe fallback
_REALIZATION: Tuple[str, ...] = ("scenes", "world")  # modules that produce a playable compile entry


def selectable_catalog() -> List[Tuple[str, str]]:
    """(id, description) for every module the proposer may choose — the always-on foundation
    (human/assets) is hidden because it is force-included regardless."""
    return [(m.id, m.description) for m in MODULE_REGISTRY.values() if m.selectable]


def _forced_ids() -> List[str]:
    return [m.id for m in MODULE_REGISTRY.values() if not m.selectable]


def expand_modules(ids) -> List[str]:
    """Force the always-on foundation in, then pull every chosen module's `requires` deps
    (transitively). Unknown ids are dropped. Order is informational — `compose` re-sorts."""
    want = _forced_ids()
    for i in list(ids):
        if i in MODULE_REGISTRY and i not in want:
            want.append(i)
    cursor = 0
    while cursor < len(want):
        m = MODULE_REGISTRY.get(want[cursor])
        for r in (m.requires if m else ()):
            if r not in want:
                want.append(r)
        cursor += 1
    return want


def engine_for(ids) -> Optional[str]:
    """The engine that can project every module in the set (Ren'Py preferred; web is the
    fallback for web-only mechanics like cards). None means no engine can build it."""
    from maestro.engines import ENGINE_TAGS, ensure_projections_registered
    ensure_projections_registered()
    for engine in ENGINE_TAGS:
        if not unprojectable(engine, ids):
            return engine
    return None


def resolve_modules(ids) -> Tuple[List[str], str]:
    """Turn the proposer's raw module picks into a buildable (modules, engine) pair: force the
    foundation, expand deps, then validate (a realization module present + projectable). On any
    inconsistency, fall back to the default visual-novel bundle so a build always exists."""
    modules = expand_modules(ids)
    has_realization = set(_REALIZATION) & set(modules)
    engine = engine_for(modules)
    if has_realization and engine is not None:
        return modules, engine
    fallback = expand_modules(_DEFAULT_MODULES)
    return fallback, engine_for(fallback) or "renpy"


# ── Engine projection registry, keyed (engine, module_id) ─────────────────────
# A module's checks are substrate-agnostic; its render is per-engine. Adding an engine registers
# projections here. A `projected` module with no projection for the chosen engine fails the compile
# fast (see `unprojectable`), never silently dropping content.
_PROJECTIONS: Dict[Tuple[str, str], Callable] = {}


def register_projection(engine: str, module_id: str, fn: Callable) -> None:
    _PROJECTIONS[(engine, module_id)] = fn


def projection_for(engine: str, module_id: str) -> Optional[Callable]:
    return _PROJECTIONS.get((engine, module_id))


def unprojectable(engine: str, module_ids) -> List[str]:
    """The composed modules that need an engine-specific renderer but have none for this engine. A
    non-empty result means the engine cannot build this game — fail fast, don't drop content."""
    missing = []
    for mid in module_ids:
        m = MODULE_REGISTRY.get(mid)
        if m is not None and m.projected and projection_for(engine, mid) is None:
            missing.append(mid)
    return missing
