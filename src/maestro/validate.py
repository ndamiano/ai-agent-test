"""validate — the agent's steering signal and completion guarantee.

Runs the typed, code-checkable done-conditions a spec declares against the durable
artifact state, and returns a structured failure list (the to-do). Recomputed each
step from durable state — the agent never remembers what's left. Empty failure list
against a frozen spec = done.

Done-conditions are data, not prose, so code can check invariants the agent declared
for genres no one pre-specified. Closed check set:
  exists        — path resolves to a non-empty value
  count         — collection size satisfies min / max / eq
  distinct      — values at path are all distinct (optionally projected by `key`)
  each_has      — every item in a collection has these subfields, non-empty
  refs_resolve  — every reference in `from` resolves to an id in `to`
  crossref      — every IR id reference resolves (cheap data-walk); ATTRIBUTES each failure to the
                  component that can fix it (see _check_crossref)
  compiles      — the artifact builds and passes the engine compile gate
"""

import json
from typing import Callable, Dict, List, Optional, Tuple

_MISSING = object()
_EMPTY = (None, "", [], {})

# (ok, detail) — detail is a human-readable reason when ok is False.
CheckResult = Tuple[bool, Optional[str]]


def _resolve(artifact: Dict, path: str):
    cur = artifact
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _hashable(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def _check_exists(artifact: Dict, c: Dict, run_dir) -> CheckResult:
    v = _resolve(artifact, c["path"])
    ok = v is not _MISSING and v not in _EMPTY
    return ok, None if ok else f"{c['path']} is missing or empty"


def _check_count(artifact: Dict, c: Dict, run_dir) -> CheckResult:
    v = _resolve(artifact, c["path"])
    if v is _MISSING or not isinstance(v, (list, dict, str)):
        return False, f"{c['path']} is not a countable collection"
    n = len(v)
    if "min" in c and n < c["min"]:
        return False, f"{c['path']} count {n} < min {c['min']}"
    if "max" in c and n > c["max"]:
        return False, f"{c['path']} count {n} > max {c['max']}"
    if "eq" in c and n != c["eq"]:
        return False, f"{c['path']} count {n} != {c['eq']}"
    return True, None


def _check_distinct(artifact: Dict, c: Dict, run_dir) -> CheckResult:
    v = _resolve(artifact, c["path"])
    if v is _MISSING or not isinstance(v, list):
        return False, f"{c['path']} is not a list"
    key = c.get("key")
    vals = [item.get(key) if (key and isinstance(item, dict)) else item for item in v]
    norm = [_hashable(x) for x in vals]
    if len(norm) == len(set(norm)):
        return True, None
    dupes = sorted({x for x in norm if norm.count(x) > 1}, key=str)
    return False, f"{c['path']} has duplicate values: {dupes[:5]}"


def _check_each_has(artifact: Dict, c: Dict, run_dir) -> CheckResult:
    v = _resolve(artifact, c["path"])
    if v is _MISSING or not isinstance(v, list):
        return False, f"{c['path']} is not a list"
    fields = c.get("fields", [])
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            return False, f"{c['path']}[{i}] is not an object"
        for f in fields:
            if not item.get(f):
                return False, f"{c['path']}[{i}] missing '{f}'"
    return True, None


def _check_refs_resolve(artifact: Dict, c: Dict, run_dir) -> CheckResult:
    src = _resolve(artifact, c["from"])
    dst = _resolve(artifact, c["to"])
    if src is _MISSING:
        return False, f"{c['from']} is missing"
    if dst is _MISSING:
        return False, f"{c['to']} is missing"

    to_key = c.get("to_key", "id")
    if isinstance(dst, dict):
        valid = set(dst.keys())
    elif isinstance(dst, list):
        valid = {d.get(to_key) if isinstance(d, dict) else d for d in dst}
    else:
        return False, f"{c['to']} is not a collection"

    from_key = c.get("from_key")
    src_items = src if isinstance(src, list) else list(src.keys()) if isinstance(src, dict) else []
    refs = [s.get(from_key) if (from_key and isinstance(s, dict)) else s for s in src_items]
    missing = [r for r in refs if r not in valid]
    if missing:
        return False, f"{c['from']} → {c['to']}: unresolved refs {missing[:5]}"
    return True, None


def _read_spec(run_dir) -> Dict:
    import json
    from pathlib import Path
    spec_path = Path(run_dir) / "spec.json"
    if spec_path.exists():
        return json.loads(spec_path.read_text(encoding="utf-8")) or {}
    return {}


def _check_compiles(artifact: Dict, c: Dict, run_dir) -> CheckResult:
    # Lint-only during the loop — distribute (packaging) is reserved for final delivery. Dispatch
    # on the spec's engine (renpy default, web for card games) so the check matches what the loop's
    # compile tool and final packaging do — a web game must not be lint-checked by the Ren'Py SDK.
    from maestro.engines import compile_for
    engine = _read_spec(run_dir).get("engine", "renpy")
    res = compile_for(engine)(run_dir, distribute=False)
    return bool(res.get("ok")), None if res.get("ok") else f"compile failed: {res.get('reason')}"


def _check_crossref(artifact: Dict, c: Dict, run_dir):
    """The cheap, ATTRIBUTED reference gate (a pure data-walk, no engine build). Every unresolved
    id reference attaches to the component that can fix it: a *reference* error (repoint to fix)
    routes to the module that owns its IR slice (a dangling opponent → matches, a dangling jump →
    nodes); a *declaration* error (declare to fix) stays on the spine, where set_*_meta lives. This
    is what keeps a cross-component failure from dead-ending in the spine's mode with the wrong
    tools — and being cheap, it runs every step and inside the single-component lock check."""
    from maestro.ir_assemble import assemble_ir
    from maestro.ir_crossref import crossref_records, slice_token, is_reference_kind
    from maestro.modules import compose, modules_for
    spec_data = _read_spec(run_dir)
    try:
        records = crossref_records(assemble_ir(artifact, spec_data.get("genre", "vn")))
    except Exception:
        # IR not assemblable yet (structure still being built) — structural checks gate this step.
        return True, None, None
    if not records:
        return True, None, None
    owner = compose(modules_for(spec_data)).slice_owner
    attributions = [
        {"component_id": owner.get(slice_token(r["path"])) if is_reference_kind(r["kind"]) else None,
         "detail": r["message"]}
        for r in records
    ]
    return False, "; ".join(r["message"] for r in records[:5]), attributions


_CHECKS: Dict[str, Callable] = {
    "exists": _check_exists,
    "count": _check_count,
    "distinct": _check_distinct,
    "each_has": _check_each_has,
    "refs_resolve": _check_refs_resolve,
    "crossref": _check_crossref,
    "compiles": _check_compiles,
}


def register_check(name: str, fn: Callable[[Dict, Dict, object], CheckResult]) -> None:
    """Register a custom check type. Genre-specific checks (e.g. renpy story structure)
    register here at startup so specs can reference them by `type` like any other check,
    while maestro itself stays genre-agnostic."""
    _CHECKS[name] = fn


# (ok, detail, attributions) — the canonical result run_check normalizes every check to. A check
# fn returns (ok, detail) for the common case (the failure belongs to the component that declared
# it) or (ok, detail, attributions) to ROUTE the failure elsewhere; attributions is a list of
# {component_id, detail}, one per cross-component sub-failure (component_id None = the declaring
# component). A 2-tuple is padded to attributions=None here, so callers read one shape.
def run_check(check: Dict, artifact: Dict, run_dir):
    # A spec is LLM-authored: a done-condition might be a bare string or otherwise malformed. Treat
    # that as a reported failure, never a crash (a 500 in the games API, or a dead build step).
    if not isinstance(check, dict):
        return False, f"malformed check (expected an object with a 'type'): {check!r}", None
    ctype = check.get("type")
    fn = _CHECKS.get(ctype)
    if fn is None:
        return False, f"unknown check type: {ctype!r}", None
    try:
        res = fn(artifact, check, run_dir)
        return res if len(res) == 3 else (res[0], res[1], None)
    except KeyError as e:
        # A missing key in the check dict itself = the spec declared a malformed check.
        return False, f"malformed {ctype} check: missing key {e}", None
    except Exception as e:
        # Any other error is a real failure to evaluate — report it honestly rather
        # than letting it crash validate or get mislabeled.
        return False, f"{ctype} check could not run: {type(e).__name__}: {e}", None


def validate(spec, state, component_id: Optional[str] = None,
             skip_types: Optional[set] = None) -> List[Dict]:
    """Return the structured failure list for the spec against durable state.

    Each failure: {component_id, check, detail}. component_id is where the failure ROUTES — a
    check may attribute it to a component other than the one that declared it (a cross-component
    reference error attaches to the slice that can fix it, not the spine that ran the gate). Empty
    list = every declared done-condition holds.

    Pass component_id to ask "is THIS component done" — every check still runs (a failure declared
    on the spine may route here), then the result is filtered to this component; the expensive
    `compiles` build is skipped, since it only ever routes to the spine (never a lock-queried
    component). skip_types omits those check types — used for a cheap live to-do mid-sub-loop.
    """
    artifact = state.load_artifact()
    skip = set(skip_types or ())
    if component_id is not None:
        skip.add("compiles")
    valid_ids = {c.get("id") for c in spec.components}
    failures: List[Dict] = []
    for comp in spec.components:
        comp_id = comp.get("id")
        for check in comp.get("done_conditions", []):
            if isinstance(check, dict) and check.get("type") in skip:
                continue
            ok, detail, attributions = run_check(check, artifact, state.run_dir)
            if ok:
                continue
            if attributions:
                for a in attributions:
                    cid = a.get("component_id") or comp_id
                    failures.append({"component_id": cid if cid in valid_ids else comp_id,
                                     "check": check, "detail": a["detail"]})
            else:
                failures.append({"component_id": comp_id, "check": check, "detail": detail})
    if component_id is not None:
        failures = [f for f in failures if f["component_id"] == component_id]
    return failures
