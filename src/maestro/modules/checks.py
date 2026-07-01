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
def reachable_from_start(artifact: Dict) -> CheckResult:
    node_ids, nodes = views.nodes_of(artifact)
    if not node_ids:
        return False, "no nodes to reach"
    edges = {nid: views.node_targets(nodes.get(nid, {})) for nid in node_ids}
    orphans = [n for n in node_ids if n not in views.reachable(node_ids, edges)]
    if orphans:
        return False, f"nodes unreachable from '{node_ids[0]}': {orphans[:5]}"
    return True, None


def node_targets_resolve(artifact: Dict) -> CheckResult:
    node_ids, nodes = views.nodes_of(artifact)
    ids = set(node_ids)
    bad = [f"{nid} -> {tgt}" for nid in node_ids
           for tgt in views.node_targets(nodes.get(nid, {})) if tgt not in ids]
    if bad:
        return False, (f"node jump/menu targets that don't exist: {bad[:5]} — either create those "
                       f"nodes (write_node) or repoint the jump to an existing node (edit_node).")
    return True, None


def min_branches(artifact: Dict, *, min=1) -> CheckResult:
    _, nodes = views.nodes_of(artifact)
    n = sum(1 for node in nodes.values() if (node.get("end", {}) or {}).get("type") == "menu")
    if n < min:
        return False, f"only {n} menu(s), need {min} — add player choices (end.type 'menu')"
    return True, None


def each_node_min_lines(artifact: Dict, *, min=3) -> CheckResult:
    node_ids, nodes = views.nodes_of(artifact)
    thin = [f"{nid} ({len(nodes.get(nid, {}).get('lines', []))})"
            for nid in node_ids if len(nodes.get(nid, {}).get("lines", [])) < min]
    if thin:
        return False, f"nodes with < {min} lines: {thin[:5]} — give them more beats"
    return True, None


def each_node_has_location(artifact: Dict) -> CheckResult:
    node_ids, nodes = views.nodes_of(artifact)
    missing = [nid for nid in node_ids if not nodes.get(nid, {}).get("location")]
    if missing:
        return False, (f"nodes with no location/background: {missing[:5]} — set each node's "
                       f"`location` to a background id (edit_node location='bg_...').")
    return True, None


def beats_realized(artifact: Dict) -> CheckResult:
    beats = [b.get("id") for b in (artifact.get("story", {}) or {}).get("beats", []) if b.get("id")]
    if not beats:
        return True, None  # no story beats — nothing to realize
    _, nodes = views.nodes_of(artifact)
    covered = {n.get("beat") for n in nodes.values() if n.get("beat")}
    missing = [b for b in beats if b not in covered]
    if missing:
        return False, (f"story beats with no scene yet: {missing} — write a node that dramatizes "
                       f"each (set the node's `beat` to that beat id). The story (LOCKED COMPONENTS) "
                       f"holds what each beat is; every beat needs at least one scene.")
    return True, None


def _node_effects(node: Dict) -> List[Dict]:
    effs = list(e for ln in node.get("lines", []) or [] for e in (ln.get("effects") or []))
    end = node.get("end", {}) or {}
    if end.get("type") == "menu":
        for ch in end.get("choices", []) or []:
            effs += ch.get("effects") or []
    return effs


def _effect_targets(eff: Dict) -> set:
    if not isinstance(eff, dict):
        return set()
    out = {eff[k] for k in ("set_flag", "clear_flag") if eff.get(k)}
    for k in ("set_var", "add_var"):
        sv = eff.get(k)
        if isinstance(sv, dict) and sv.get("var"):
            out.add(sv["var"])
    return out


def _cond_state_refs(cond) -> set:
    if not isinstance(cond, dict):
        return set()
    refs = {cond[k] for k in ("var", "flag") if cond.get(k)}
    for key in ("all", "any"):
        for c in cond.get(key, []) or []:
            refs |= _cond_state_refs(c)
    if "not" in cond:
        refs |= _cond_state_refs(cond["not"])
    return refs


def no_dead_gates(artifact: Dict) -> CheckResult:
    _, nodes = views.nodes_of(artifact)
    set_in: Dict[str, set] = {}
    for nid, node in nodes.items():
        for eff in _node_effects(node):
            for t in _effect_targets(eff):
                set_in.setdefault(t, set()).add(nid)
    dead = []
    for nid, node in nodes.items():
        end = node.get("end", {}) or {}
        if end.get("type") != "menu":
            continue
        for ch in end.get("choices", []) or []:
            for ref in _cond_state_refs(ch.get("requires")):
                if not (set_in.get(ref, set()) - {nid}):
                    dead.append(f"{nid} (gates on '{ref}')")
    if dead:
        return False, (f"choices gated on state that is never raised in an earlier scene: {dead[:5]} "
                       f"— the gate can't open, so the branch is dead. Either raise it with an effect "
                       f"in an EARLIER node (add_var/set_flag), or DROP the `requires` so the choice "
                       f"is always available (endings can be earned by the story, not a variable).")
    return True, None


def all_characters_speak(artifact: Dict) -> CheckResult:
    # `isinstance str` guards: a mis-typed speaker/id (the model nesting an object) must not crash the
    # set build with `unhashable type: 'dict'` — it's excluded and caught by the schema/other checks.
    chars = {c.get("id") for c in artifact.get("characters", {}).get("characters", [])
             if isinstance(c, dict) and isinstance(c.get("id"), str)}
    if not chars:
        return False, "characters component has no characters"
    _, nodes = views.nodes_of(artifact)
    spoke = {ln.get("speaker") for node in nodes.values()
             for ln in node.get("lines", []) if isinstance(ln, dict) and isinstance(ln.get("speaker"), str)}
    silent = sorted(chars - spoke)
    if silent:
        return False, f"characters who never speak: {silent} — give them lines"
    return True, None


def places_reachable(artifact: Dict) -> CheckResult:
    place_ids, places, pc = views.places_of(artifact)
    if not place_ids:
        return False, "no places to reach"
    reach = views.reachable_places(place_ids, places, pc.get("start_place"))
    orphans = [p for p in place_ids if p not in reach]
    if orphans:
        srcs = sorted(reach)[:3] or [pc.get("start_place")]
        return False, (
            f"places unreachable from start: {orphans[:5]}. In a REACHABLE place (one of {srcs}) "
            f"ADD a NEW move hotspot pointing AT the orphan — do NOT repoint an existing hotspot "
            f"(that breaks its current route). e.g. add_interactable(place_id=\"{srcs[0]}\", "
            f'interactable={{"id":"h_to_{orphans[0]}","label":"<exit>",'
            f'"position":{{"rect":{{"x":1040,"y":560,"w":180,"h":120}}}},'
            f'"action":{{"type":"move","target":"{orphans[0]}"}}}}). '
            f"The move must live in a REACHABLE place and point AT the orphan, not the reverse.")
    return True, None


def each_place_min_interactables(artifact: Dict, *, min=2) -> CheckResult:
    place_ids, places, _ = views.places_of(artifact)
    thin = [f"{pid} ({len(places.get(pid, {}).get('interactables', []))})"
            for pid in place_ids if len(places.get(pid, {}).get("interactables", [])) < min]
    if thin:
        return False, f"places with < {min} interactables: {thin[:5]}"
    return True, None


# ── state wiring: every declared scalar/item has a producer AND a consumer ────
def _scan_effects(effs, host: str, rec) -> None:
    for eff in effs or []:
        if not isinstance(eff, dict):
            continue
        for k in ("set_flag", "clear_flag"):
            if eff.get(k):
                rec(eff[k], "flag", "prod", host)
        for k in ("set_var", "add_var"):
            sv = eff.get(k)
            if isinstance(sv, dict):
                rec(sv.get("var"), "variable", "prod", host)
        if eff.get("add_item"):
            rec(eff["add_item"], "item", "prod", host)
        if eff.get("remove_item"):
            rec(eff["remove_item"], "item", "cons", host)


def _scan_condition(cond, host: str, rec) -> None:
    for ref in _cond_state_refs(cond):           # flags + variables read by a gate
        rec(ref, None, "cons", host)
    for it in views.cond_items(cond):            # items a clause requires
        rec(it, "item", "cons", host)


def _walk_state(artifact: Dict) -> Dict:
    """For every flag / variable / item id, gather the host components that PRODUCE it (set/add/take),
    CONSUME it (gate/use), and DECLARE it (an explicit catalog/meta entry)."""
    info: Dict = {}

    def rec(sid, kind, slot: str, host: str) -> None:
        if not sid or not isinstance(sid, str):
            return
        e = info.setdefault(sid, {"kind": kind or "state", "prod": set(), "cons": set(), "decl": set()})
        if kind:
            e["kind"] = kind
        e[slot].add(host)

    _, nodes = views.nodes_of(artifact)
    for n in nodes.values():
        _scan_effects(_node_effects(n), "nodes", rec)
        end = n.get("end", {}) or {}
        if end.get("type") == "menu":
            for ch in end.get("choices", []) or []:
                _scan_condition(ch.get("requires"), "nodes", rec)

    pc = artifact.get("places") or {}
    for f in (pc.get("flags") or []):
        rec(f, "flag", "decl", "places")
    for v in (pc.get("variables") or []):
        rec(v.get("id") if isinstance(v, dict) else v, "variable", "decl", "places")
    goal = pc.get("goal")
    if isinstance(goal, dict) and goal.get("type") == "flag":
        rec(goal.get("id"), "flag", "cons", "places")        # the win condition reads it
    for p in (pc.get("places") or {}).values():
        for h in p.get("interactables", []):
            a = h.get("action", {}) or {}
            if a.get("type") == "take" and a.get("item"):
                rec(a["item"], "item", "prod", "places")
            _scan_effects(views.action_effects(a), "places", rec)
            for cond in views.action_conditions(a):
                _scan_condition(cond, "places", rec)

    for it in (artifact.get("items") or {}).get("items", []) or []:
        if isinstance(it, dict):
            rec(it.get("id"), "item", "decl", "items")

    return info


def _pick_host(*slots: set) -> str:
    union = set().union(*slots)
    return sorted(union)[0] if union else "nodes"


def state_wiring(artifact: Dict) -> List[Dict]:
    """Every declared/used scalar or item must have BOTH a producer (a way it's set/added/taken) and
    a consumer (a gate/use). Returns [{component, ref, message}] — a missing producer is a dangling
    reference; a missing consumer is dead state to use or cut. Both are fixed on a host component."""
    out: List[Dict] = []
    for sid, e in sorted(_walk_state(artifact).items()):
        kind = e["kind"]
        if not e["prod"]:
            out.append({"component": _pick_host(e["cons"], e["decl"]), "ref": sid, "message": (
                f"{kind} '{sid}' is read or declared but nothing ever produces it — set/add/take it "
                f"where it should change (an effect or a take hotspot), or drop the reference.")})
        if not e["cons"]:
            out.append({"component": _pick_host(e["prod"], e["decl"]), "ref": sid, "message": (
                f"{kind} '{sid}' is produced or declared but never used — gate a choice/hotspot on it, "
                f"or cut it. Use it or cut it.")})
    return out


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
