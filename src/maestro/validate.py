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
  compiles      — the artifact builds and passes the compile_renpy gate
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


def _check_compiles(artifact: Dict, c: Dict, run_dir) -> CheckResult:
    from renpy.compiler import compile_renpy
    res = compile_renpy(run_dir)
    return bool(res.get("ok")), None if res.get("ok") else f"compile failed: {res.get('reason')}"


_CHECKS: Dict[str, Callable[[Dict, Dict, object], CheckResult]] = {
    "exists": _check_exists,
    "count": _check_count,
    "distinct": _check_distinct,
    "each_has": _check_each_has,
    "refs_resolve": _check_refs_resolve,
    "compiles": _check_compiles,
}


def run_check(check: Dict, artifact: Dict, run_dir) -> CheckResult:
    fn = _CHECKS.get(check.get("type"))
    if fn is None:
        return False, f"unknown check type: {check.get('type')!r}"
    try:
        return fn(artifact, check, run_dir)
    except KeyError as e:
        return False, f"malformed {check.get('type')} check: missing {e}"


def validate(spec, state, component_id: Optional[str] = None) -> List[Dict]:
    """Return the structured failure list for the spec against durable state.

    Each failure: {component_id, check, detail}. Empty list = every declared
    done-condition holds. Pass component_id to validate a single component.
    """
    artifact = state.load_artifact()
    failures: List[Dict] = []
    for comp in spec.components:
        if component_id is not None and comp.get("id") != component_id:
            continue
        for check in comp.get("done_conditions", []):
            ok, detail = run_check(check, artifact, state.run_dir)
            if not ok:
                failures.append({"component_id": comp.get("id"), "check": check, "detail": detail})
    return failures
