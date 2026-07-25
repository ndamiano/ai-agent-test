"""CONFORMANCE — does the code obey the contracts the MODEL declared for itself?

Every rule checked here comes out of `interfaces.json`. There is no game knowledge in this file — it
answers only whether the source is consistent with its own architecture. When the declaration turns
out to be the mistake, amending it is a legal repair.

Violations reported:
  OWNERSHIP  — a function REPLACES a state field it does not own
  LIFETIME   — a lifetime=run field is replaced by a shorter-lived function (progress destroyed)
  ELEMTYPE   — a value taken out of a T[] is pushed into a U[]
  MISSING    — a declared function was never implemented
  UNEXPORTED — a declared function is implemented but not exported, so no other file can reach it

`tsc` cannot see any of these: every one is type-correct code that wires the game wrong.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# `function f(`, `export function f(`, `export async function f(` — and the arrow form the model
# reaches for on small helpers, `export const f = (…) => {`.
_FUNC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*[(<]"
    r"|^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s*)?\([^)]*\)\s*"
    r"(?::[^=>\n]+)?=>\s*\{",
    re.M)
# state.deck = …  /  s.deck = …  /  g.deck = …   (a REPLACEMENT, not a mutation)
_ASSIGN_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*=(?![=>])")
_MUTATE_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*(?:\+\+|--|[-+*/]=|\."
    r"(?:push|pop|shift|unshift|splice|sort|reverse|fill)\s*\()")
_GENERATED = "// GENERATED"
# `export function f` / `export const f` / `export { f, g as h }` / `export default function f`.
_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)"
    r"|^\s*export\s*\{([^}]*)\}",
    re.M)


def _exported(src: str) -> set:
    """Names a module actually exposes. A declared function that is only local is unreachable from
    the file the architecture says calls it — the contract exists, the wiring does not."""
    out = set()
    for direct, braced in _EXPORT_RE.findall(src):
        if direct:
            out.add(direct)
        for part in braced.split(","):
            name = part.split(" as ")[0].strip()   # the LOCAL name — what the file implements
            if name:
                out.add(name)
    return out


def _split_functions(src: str) -> Dict[str, tuple]:
    """Map function name -> (start, end) by brace-counting from each declaration."""
    spans = {}
    for m in _FUNC_RE.finditer(src):
        name = m.group(1) or m.group(2)
        i = src.find("{", m.end() - 1)
        if i < 0:
            continue
        depth, j = 0, i
        while j < len(src):
            c = src[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        spans[name] = (m.start(), j)
    return spans


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _is_container(info: Dict) -> bool:
    """Arrays, objects and maps ACCUMULATE; numbers, strings and booleans do not.

    Scalars are clamped, recomputed and derived constantly, so `state.hp = 100` is not a violation.
    """
    t = (info.get("type") or "").lower()
    return any(k in t for k in ("[]", "array", "list", "object", "map", "record", "{", "set<"))


def _rhs_of(body: str, eq_pos: int) -> str:
    depth, i, out = 0, eq_pos + 1, []
    while i < len(body):
        c = body[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c in ";\n" and depth == 0:
            break
        out.append(c)
        i += 1
    return "".join(out)


def _is_update(body: str, eq_pos: int, prop: str) -> bool:
    """True when the assignment READS the field it writes — `state.hand = state.hand.filter(…)`.

    An update preserves what accumulated; only an assignment that ignores the current value destroys
    it.
    """
    return re.search(rf"\.\s*{re.escape(prop)}\b", _rhs_of(body, eq_pos)) is not None


_ELEM_RE = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*\[\]\s*$")
_TAKE_RE = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*[^;\n]*?"
    r"\.\s*([A-Za-z_$][\w$]*)\s*(?:\.\s*(?:pop|shift)\s*\(\s*\)|\[)")
_PUSH_RE = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\.\s*(?:push|unshift)\s*\(\s*([A-Za-z_$][\w$]*)\s*\)")


def _elem_type(info: Dict) -> Optional[str]:
    m = _ELEM_RE.match(info.get("type") or "")
    return m.group(1) if m else None


def _check_element_types(path, src, spans, state, alias, violations) -> None:
    """A value taken out of a T[] and pushed into a U[] is a contract break that is SILENT at runtime.

    The two halves are authored in separate turns against the same declared types, so nothing catches
    it: the reader asks an id for a field only the full object has, gets undefined, and draws nothing.
    """
    for fname, (start, end) in spans.items():
        body = src[start:end]
        origin = {}
        for m in _TAKE_RE.finditer(body):
            var, prop = m.group(1), m.group(2)
            field = alias.get(prop)
            if field and _elem_type(state[field]):
                origin[var] = (_elem_type(state[field]), field)
        for m in _PUSH_RE.finditer(body):
            prop, var = m.group(1), m.group(2)
            field = alias.get(prop)
            if not field or var not in origin:
                continue
            want = _elem_type(state[field])
            got, from_field = origin[var]
            if not want or want == got:
                continue
            violations.append({
                "kind": "ELEMTYPE", "file": path, "line": _line_of(src, start + m.start()),
                "msg": (f"{fname}() pushes {var} into {prop}, but you declared '{field}' as {want}[] "
                        f"and {var} came out of '{from_field}' which is {got}[]. Whatever reads "
                        f"{field} will expect a {want} and get a {got}."),
            })


def _alias_map(state: Dict) -> Dict[str, str]:
    """Property name -> declared field.

    The regexes see the LAST property of `state.fight.hand`, so a dotted declaration must be reachable
    by its leaf too or nested state matches nothing at all. A leaf shared by two fields is ambiguous
    and maps to neither — a wrong attribution is worse than a missed one. snake_case declarations are
    also reachable as camelCase, because the model declares in one style and writes TS in the other.
    """
    alias, leaves = {}, {}
    for field in state:
        for name in (field, re.sub(r"_(\w)", lambda m: m.group(1).upper(), field)):
            alias[name] = field
        leaf = field.rsplit(".", 1)[-1]
        for name in (leaf, re.sub(r"_(\w)", lambda m: m.group(1).upper(), leaf)):
            leaves.setdefault(name, set()).add(field)
    for name, fields in leaves.items():
        if len(fields) == 1 and name not in alias:
            alias[name] = next(iter(fields))
    return alias


def check(iface: Dict, sources: Dict[str, str]) -> List[Dict]:
    """Sweep `sources` against the architecture. Returns a list of violation dicts.

    GENERATED files are skipped: the tools refuse to edit them, and the scaffold legitimately drives
    fields the model's own functions must not.
    """
    state = {f["field"]: f for f in iface.get("state", []) if f.get("field")}
    funcs = {f["name"]: f for f in iface.get("functions", []) if f.get("name")}
    alias = _alias_map(state)

    violations: List[Dict] = []
    implemented: Dict[str, str] = {}
    unexported: Dict[str, str] = {}

    for path, src in sources.items():
        if src.lstrip().startswith(_GENERATED):
            continue
        spans = _split_functions(src)
        exported = _exported(src)
        for name in spans:
            implemented[name] = path
            if name not in exported:
                unexported.setdefault(name, path)
            else:
                unexported.pop(name, None)
        _check_element_types(path, src, spans, state, alias, violations)
        for fname, (start, end) in spans.items():
            body = src[start:end]
            for rx, kind in ((_ASSIGN_RE, "replace"), (_MUTATE_RE, "mutate")):
                for m in rx.finditer(body):
                    prop = m.group(2)
                    field = alias.get(prop)
                    if not field:
                        continue
                    info = state[field]
                    owner = info.get("owner")
                    if kind == "replace" and _is_update(body, m.end() - 1, prop):
                        continue
                    if not _is_container(info) or kind != "replace":
                        continue
                    if owner and fname != owner:
                        line = _line_of(src, start + m.start())
                        violations.append({
                            "kind": "OWNERSHIP", "file": path, "line": line,
                            "msg": (f"{fname}() replaces {prop}, but you declared the owner of "
                                    f"'{field}' to be {owner}(). Either mutate it in place, move the "
                                    f"assignment into {owner}(), or amend the declaration if {fname}() "
                                    f"is the real owner."),
                        })
                        if info.get("lifetime") == "run":
                            violations.append({
                                "kind": "LIFETIME", "file": path, "line": line,
                                "msg": (f"'{field}' is declared lifetime=run — it must survive the "
                                        f"whole run — but {fname}() replaces it. Everything "
                                        f"accumulated in it is destroyed."),
                            })

    for name in funcs:
        if name not in implemented:
            violations.append({"kind": "MISSING", "file": funcs[name].get("file") or "-", "line": 0,
                               "msg": f"you declared {name}() but no file implements it"})
        elif name in unexported:
            violations.append({
                "kind": "UNEXPORTED", "file": unexported[name], "line": 0,
                "msg": (f"you declared {name}() in the architecture, so it is part of this game's "
                        f"wiring, but {unexported[name]} implements it without exporting it — no "
                        f"other file can reach it.")})

    # One report per (kind, message): the same line often trips several patterns.
    seen, unique = set(), []
    for v in violations:
        key = (v["kind"], v["msg"])
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique
