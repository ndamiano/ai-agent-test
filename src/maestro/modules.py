"""Mechanic-modules — the composable unit a game is built from.

A game is a *substrate* (execution model — discrete_state today; the AI never invents one)
plus a *composed set of mechanic-modules* the spec selects. Each module bundles what used to
be scattered, hardcoded-by-`genre` registries into one object:

  components  — the on-disk component ids it owns (a *vocabulary* module owns none)
  schemas     — structural validators (component_id -> validator(content) -> err|None)
  skeletons   — authoring shapes shown to the agent (component_id -> str)
  baseline    — code-enforced done-conditions (component_id -> [check, ...])
  deps        — intrinsic build order (component_id -> [dep ids])
  checks      — custom validate check types (name -> fn) registered into maestro.validate
  tool_names  — decider tool-schema names this module contributes
  assemble    — (artifact, ir) -> mutate the IR with this module's slice
  crossref    — (ir) -> [error str] reference checks
  sub_runner  — stateful sub-loop factory (dialogue->nodes, navigation->places)
  projector   — executor view fn for its owned component
  action_verbs— action verb names this module adds to navigation's verb set

`compose(module_ids)` unions the active modules into one bundle every former genre-keyed
lookup now reads. Engine *projections* (IR -> Ren'Py / IR -> web runtime) are NOT on the
module — they are per-engine and register separately (see register_projection), because a
module's schema is substrate-agnostic while its projection is per-target.

This is the substrate-level seam: module defs live above the engine backends (maestro.discrete)
so Ren'Py and web share them; an engine only adds a projection.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from maestro.validate import register_check


@dataclass(frozen=True)
class Module:
    id: str
    substrates: Tuple[str, ...] = ("discrete",)
    components: Tuple[str, ...] = ()
    schemas: Dict[str, Callable] = field(default_factory=dict)
    skeletons: Dict[str, str] = field(default_factory=dict)
    baseline: Dict[str, List[Dict]] = field(default_factory=dict)
    deps: Dict[str, List[str]] = field(default_factory=dict)
    checks: Dict[str, Callable] = field(default_factory=dict)
    tool_names: Tuple[str, ...] = ()
    assemble: Optional[Callable] = None
    crossref: Optional[Callable] = None
    sub_runner: Optional[Callable] = None
    projector: Optional[Callable] = None
    action_verbs: Tuple[str, ...] = ()
    # True if this module's content needs an engine-specific renderer beyond plain IR assembly
    # (dialogue/navigation/card_play). Such a module can only build on an engine that registered
    # a projection for it — otherwise the compile fails fast instead of silently dropping content.
    projected: bool = False


MODULE_REGISTRY: Dict[str, Module] = {}


def register_module(m: Module) -> None:
    MODULE_REGISTRY[m.id] = m
    for name, fn in m.checks.items():
        register_check(name, fn)


# A named preset = (substrate, module ids, default engine). The classifier emits one of these;
# `genre` survives as the preset name for the UI, but the build keys off the composed modules.
@dataclass(frozen=True)
class Preset:
    substrate: str
    modules: Tuple[str, ...]
    engine: str = "renpy"


PRESETS: Dict[str, Preset] = {}


def register_preset(name: str, preset: Preset) -> None:
    PRESETS[name] = preset


def _identity(check: Dict):
    return (check.get("type"), check.get("path"), check.get("from"))


def _merge_baseline(into: Dict[str, List[Dict]], add: Dict[str, List[Dict]]) -> None:
    """Union a module's baseline into the accumulator, per component. Two checks with the same
    (type, path, from) identity are the same check — raise its `min` to the max, and union the
    `each_has` field sets (so cast can require [id,name] and dialogue add the richer fields on
    the same path without one clobbering the other)."""
    for cid, checks in add.items():
        existing = into.setdefault(cid, [])
        index = {_identity(c): c for c in existing}
        for base in checks:
            cur = index.get(_identity(base))
            if cur is None:
                copy = dict(base)
                if isinstance(copy.get("fields"), list):
                    copy["fields"] = list(copy["fields"])
                existing.append(copy)
                index[_identity(base)] = copy
            else:
                if "min" in base:
                    cur["min"] = max(cur.get("min", base["min"]), base["min"])
                if isinstance(base.get("fields"), list):
                    merged = list(cur.get("fields", []))
                    for f in base["fields"]:
                        if f not in merged:
                            merged.append(f)
                    cur["fields"] = merged


@dataclass
class Composed:
    module_ids: Tuple[str, ...]
    components: List[str]
    schemas: Dict[str, Callable]
    skeletons: Dict[str, str]
    baseline: Dict[str, List[Dict]]
    deps: Dict[str, List[str]]
    tool_names: List[str]
    assemblers: List[Callable]
    crossrefs: List[Callable]
    sub_runners: Dict[str, Callable]   # component_id -> subloop factory
    projectors: Dict[str, Callable]    # component_id -> view fn
    action_verbs: List[str]


def compose(module_ids) -> Composed:
    """Union the active modules into one bundle. Order follows module_ids; later modules add to
    earlier ones (baseline merge dedupes/raises, never drops)."""
    components: List[str] = []
    schemas: Dict[str, Callable] = {}
    skeletons: Dict[str, str] = {}
    baseline: Dict[str, List[Dict]] = {}
    deps: Dict[str, List[str]] = {}
    tool_names: List[str] = []
    assemblers: List[Callable] = []
    crossrefs: List[Callable] = []
    sub_runners: Dict[str, Callable] = {}
    projectors: Dict[str, Callable] = {}
    action_verbs: List[str] = []

    for mid in module_ids:
        m = MODULE_REGISTRY.get(mid)
        if m is None:
            raise KeyError(f"unknown module {mid!r} (registered: {sorted(MODULE_REGISTRY)})")
        for c in m.components:
            if c not in components:
                components.append(c)
        schemas.update(m.schemas)
        skeletons.update(m.skeletons)
        _merge_baseline(baseline, m.baseline)
        for cid, dlist in m.deps.items():
            cur = deps.setdefault(cid, [])
            for d in dlist:
                if d not in cur:
                    cur.append(d)
        for name in m.tool_names:
            if name not in tool_names:
                tool_names.append(name)
        if m.assemble is not None:
            assemblers.append(m.assemble)
        if m.crossref is not None:
            crossrefs.append(m.crossref)
        if m.sub_runner is not None:
            for c in m.components:
                sub_runners[c] = m.sub_runner
        if m.projector is not None:
            for c in m.components:
                projectors[c] = m.projector
        for v in m.action_verbs:
            if v not in action_verbs:
                action_verbs.append(v)

    return Composed(
        module_ids=tuple(module_ids), components=components, schemas=schemas,
        skeletons=skeletons, baseline=baseline, deps=deps, tool_names=tool_names,
        assemblers=assemblers, crossrefs=crossrefs, sub_runners=sub_runners,
        projectors=projectors, action_verbs=action_verbs)


def modules_for(spec: Dict) -> Tuple[str, ...]:
    """The module ids a spec drives: explicit `modules` if present, else the `genre` preset's
    default set (default 'vn'). Keeps specs that predate the modules field working."""
    mods = spec.get("modules")
    if mods:
        return tuple(mods)
    preset = PRESETS.get(spec.get("genre", "vn"))
    return preset.modules if preset else PRESETS["vn"].modules


# ── per-engine projection registry ────────────────────────────────────────────
# A projection turns a module's IR slice into engine output. Keyed (engine, module_id). An
# engine that has no projection for a module in the composition can't build it — the dispatch
# fails fast with a clear message rather than silently dropping content.
_PROJECTIONS: Dict[Tuple[str, str], Callable] = {}


def register_projection(engine: str, module_id: str, fn: Callable) -> None:
    _PROJECTIONS[(engine, module_id)] = fn


def projection_for(engine: str, module_id: str) -> Optional[Callable]:
    return _PROJECTIONS.get((engine, module_id))


def unprojectable(engine: str, module_ids) -> List[str]:
    """The composed modules that need an engine-specific renderer but have none for this engine.
    A non-empty result means the engine cannot build this game — fail fast, don't drop content."""
    missing = []
    for mid in module_ids:
        m = MODULE_REGISTRY.get(mid)
        if m is not None and m.projected and projection_for(engine, mid) is None:
            missing.append(mid)
    return missing
