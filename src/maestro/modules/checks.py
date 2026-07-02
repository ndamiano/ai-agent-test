"""The check library a module's `get_errors` composes.

Each predicate takes the assembled artifact (and the relevant param values) and returns
`(ok, detail)` — `detail` is the human-readable reason when it fails. A module wraps a failed
predicate into a typed `Error` with `as_error`, choosing the tier (BUILD = make it exist, FIX =
make it correct) and the component it lands on. Reference integrity (`crossref_failures`) and the
terminal engine build (`compile_failure`) are the two heavier checks the projected modules append.
"""

import json
from typing import Dict, List, Optional, Tuple

from maestro.modules.module import Error, ErrorType
from maestro.modules import views

_MISSING = object()
_EMPTY = (None, "", [], {})
CheckResult = Tuple[bool, Optional[str]]


def as_error(result: CheckResult, *, type: ErrorType, code: str, component: str,
             path: Optional[str] = None, ref: Optional[str] = None) -> Optional[Error]:
    """Wrap a failed `(ok, detail)` predicate into a typed Error; None when it passed."""
    ok, detail = result
    if ok:
        return None
    return Error(type=type, code=code, component=component, message=detail or code,
                 path=path, ref=ref)


def length(artifact: Dict, path: str) -> int:
    v = _resolve(artifact, path)
    return len(v) if isinstance(v, (list, dict, str)) else 0


def slot_errors(n: int, *, type: ErrorType, code: str, component: str, noun: str) -> List[Error]:
    """Fan a count shortfall into N per-slot create-errors — one authored item per step, instead of
    one opaque 'need N' error the loop couldn't see progress on. A distinct `path` per slot gives
    each a stable identity, so authoring one shrinks the set (visible progress, no false stall)
    while every still-owed slot persists. The write tool's slot guard decides WHICH concrete item
    each slot becomes; the slot error only says 'one more is owed'."""
    return [Error(type=type, code=code, component=component, path=f"#{k:03d}",
                  message=f"author one more {noun} — {n} still needed to reach the target")
            for k in range(1, n + 1)]


# ── generic primitives (path-addressed data checks) ──────────────────────────
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


def exists(artifact: Dict, path: str) -> CheckResult:
    v = _resolve(artifact, path)
    ok = v is not _MISSING and v not in _EMPTY
    return ok, None if ok else f"{path} is missing or empty"


def count(artifact: Dict, path: str, *, min=None, max=None, eq=None) -> CheckResult:
    v = _resolve(artifact, path)
    if v is _MISSING or not isinstance(v, (list, dict, str)):
        return False, f"{path} is not a countable collection"
    n = len(v)
    if min is not None and n < min:
        return False, f"{path} count {n} < min {min}"
    if max is not None and n > max:
        return False, f"{path} count {n} > max {max}"
    if eq is not None and n != eq:
        return False, f"{path} count {n} != {eq}"
    return True, None


def distinct(artifact: Dict, path: str, *, key=None) -> CheckResult:
    v = _resolve(artifact, path)
    if v is _MISSING or not isinstance(v, list):
        return False, f"{path} is not a list"
    vals = [item.get(key) if (key and isinstance(item, dict)) else item for item in v]
    norm = [_hashable(x) for x in vals]
    if len(norm) == len(set(norm)):
        return True, None
    dupes = sorted({x for x in norm if norm.count(x) > 1}, key=str)
    return False, f"{path} has duplicate values: {dupes[:5]}"


def each_has(artifact: Dict, path: str, *, fields: List[str]) -> CheckResult:
    v = _resolve(artifact, path)
    if v is _MISSING or not isinstance(v, list):
        return False, f"{path} is not a list"
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            return False, f"{path}[{i}] is not an object"
        for f in fields:
            if not item.get(f):
                return False, f"{path}[{i}] missing '{f}'"
    return True, None


def refs_resolve(artifact: Dict, frm: str, to: str, *, from_key=None, to_key="id") -> CheckResult:
    src = _resolve(artifact, frm)
    dst = _resolve(artifact, to)
    if src is _MISSING:
        return False, f"{frm} is missing"
    if dst is _MISSING:
        return False, f"{to} is missing"
    if isinstance(dst, dict):
        valid = set(dst.keys())
    elif isinstance(dst, list):
        valid = {d.get(to_key) if isinstance(d, dict) else d for d in dst}
    else:
        return False, f"{to} is not a collection"
    src_items = src if isinstance(src, list) else list(src.keys()) if isinstance(src, dict) else []
    refs = [s.get(from_key) if (from_key and isinstance(s, dict)) else s for s in src_items]
    missing = [r for r in refs if r not in valid]
    if missing:
        return False, f"{frm} → {to}: unresolved refs {missing[:5]}"
    return True, None


# ── structural story/game checks (graph walks over nodes / places) ───────────
def node_effects(node: Dict) -> List[Dict]:
    effs = list(e for ln in node.get("lines", []) or [] for e in (ln.get("effects") or []))
    end = node.get("end", {}) or {}
    if end.get("type") == "menu":
        for ch in end.get("choices", []) or []:
            effs += ch.get("effects") or []
    return effs


def effect_targets(eff: Dict) -> set:
    if not isinstance(eff, dict):
        return set()
    out = {eff[k] for k in ("set_flag", "clear_flag") if eff.get(k)}
    for k in ("set_var", "add_var"):
        sv = eff.get(k)
        if isinstance(sv, dict) and sv.get("var"):
            out.add(sv["var"])
    return out


def cond_state_refs(cond) -> set:
    if not isinstance(cond, dict):
        return set()
    refs = {cond[k] for k in ("var", "flag") if isinstance(cond.get(k), str)}
    for key in ("all", "any"):
        for c in cond.get(key, []) or []:
            refs |= cond_state_refs(c)
    if "not" in cond:
        refs |= cond_state_refs(cond["not"])
    return refs


# ── state wiring: every declared scalar/item has a producer AND a consumer ────
# ── reference integrity + terminal build (the two heavy checks) ──────────────
def crossref_failures(artifact: Dict):
    """Every IR id reference resolves — a cheap data-walk, no engine build. Returns a list of
    {message, path, ref} for the unresolved references (empty = clean). The IR may not be
    assemblable mid-build (structure still forming); that's not a crossref failure, so it returns
    []. The caller (a projected module) wraps each into a FIX Error on its component."""
    from maestro.ir_assemble import assemble_ir
    from maestro.ir_crossref import crossref_records
    try:
        records = crossref_records(assemble_ir(artifact))
    except Exception:
        return []
    return [{"message": r["message"], "path": r.get("path"), "ref": r.get("ref")} for r in records]


def compile_failure(run_dir, engine: str) -> CheckResult:
    """The terminal FIX check: the artifact builds and passes the engine compile gate (lint-only
    during the loop; packaging is reserved for delivery)."""
    from maestro.engines import compile_for
    res = compile_for(engine)(run_dir, distribute=False)
    return bool(res.get("ok")), None if res.get("ok") else f"compile failed: {res.get('reason')}"
