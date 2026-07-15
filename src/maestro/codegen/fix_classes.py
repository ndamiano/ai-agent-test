"""Fix classes — the error-class → fixer map (the codegen analog of IR's per-check prompt).

A GATE detects a raw failure; a FIX CLASS resolves it. IR could own a total `check-code → prompt`
map because it emitted every error itself. Codegen can't: tsc emits an open vocabulary of thousands
of codes we don't own, and a tsc error pins the SITE but underdetermines the REPAIR (`health` missing
on `Player` is fixed by adding a field OR — usually — by renaming the caller to the real `hp`; the
message can't say which). So the ownership axis here is not the code but the AUTHORITY that decides
the correct fix. A class is chosen by matching the Error (its `kind` for our own gates, the TS code in
its message for tsc) and owns:
  - a DIRECTIVE: a short root-cause framing ("this is a wrong name, not a missing field")
  - an AUTHORITY block: the on-disk context that biases toward the correct fix (the referenced type's
    real members + which names already dominate + the spec) — the thing the raw error omits
  - an optional DETERMINISTIC pre-pass (a safe bulk collapse before spending an LLM call)

The read→write loop is shared; a class only supplies these three. `default` matches everything and
carries none of them — it IS today's generic loop, so an unclassified failure degrades to the status
quo, never worse. Add a class = one entry here; the map stays small and closed because only the bare-
label failures need it (tsc's self-describing logic bugs are handled by `default` reading the site).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from maestro.codegen.gates import game_files, reconcile_types

_PROMPTS = Path(__file__).resolve().parent / "prompts" / "fix_kinds"


@dataclass(frozen=True)
class FixClass:
    id: str
    matches: Callable                          # (error) -> bool
    directive: str = ""                        # root-cause framing injected into the fix's user msg
    authority: Optional[Callable] = None       # (spec, run_dir, error) -> str  (the authority block)
    deterministic: Optional[Callable] = None   # (run_dir, error) -> {changes,count} | None


def _directive(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8") if name else ""


# ── contract-mismatch ─────────────────────────────────────────────────────────
# A file references a field / type / export the shared types do not provide. tsc names the SITE; the
# fix is usually to reconcile the CALLER to what already exists, not to invent the missing thing.
_CONTRACT_CODES = ("TS2339", "TS2551", "TS2353", "TS2561", "TS2741", "TS2739",
                   "TS2322", "TS2305", "TS2724")

_PROP = re.compile(r"Property '([^']+)' does not exist on type '([^']+)'")
_LIT = re.compile(r"'([^']+)' does not exist in type '([^']+)'")
_REQ = re.compile(r"Property '([^']+)' is missing in type '[^']*' but required in type '([^']+)'")
_EXPORT = re.compile(r"has no exported member(?: named)? '([^']+)'")


def _matches_contract(error) -> bool:
    return any(c in (error.message or "") for c in _CONTRACT_CODES)


def _extract_decl(files: dict, typ: str) -> str:
    """The real definition of `typ` across the game — an interface/enum block (brace-balanced so a
    nested object field can't end it early) or a `type X = …;` alias. The members the caller must
    reconcile to; empty if the type isn't defined in the game (a kit ambient type)."""
    for src in files.values():
        m = re.search(rf"export\s+(?:interface|type|enum)\s+{re.escape(typ)}\b", src)
        if not m:
            continue
        brace, semi = src.find("{", m.end()), src.find(";", m.end())
        if brace != -1 and (semi == -1 or brace < semi):
            depth, j, n = 0, brace, len(src)
            while j < n:
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        return src[m.start():j + 1]
                j += 1
        return src[m.start():semi + 1] if semi != -1 else src[m.start():m.end()]
    return ""


def _members(decl: str) -> list:
    body = decl[decl.find("{") + 1: decl.rfind("}")] if "{" in decl else ""
    return re.findall(r"(?:^|[{;\n])\s*([A-Za-z_]\w*)\s*[?!]?\s*[:=]", body)


def _member_uses(files: dict, name: str) -> int:
    return sum(len(re.findall(rf"\.{re.escape(name)}\b", src)) for src in files.values())


def _contract_authority(spec, run_dir, error) -> str:
    """The block the raw tsc error omits: for each (type, wrong-field) it names, the type's REAL
    definition + how often each real member is accessed vs the wrong name — so the model reconciles
    the caller to an existing field instead of adding a duplicate one (hp vs health)."""
    files = game_files(run_dir)
    msg = error.message or ""
    blocks, seen = [], set()
    for rx in (_PROP, _LIT, _REQ):
        for fld, typ in rx.findall(msg):
            if (typ, fld) in seen:
                continue
            seen.add((typ, fld))
            decl = _extract_decl(files, typ)
            uses = sorted(((_member_uses(files, m), m) for m in _members(decl)), reverse=True)
            member_lines = "\n".join(f"    .{m}: {n} use(s)" for n, m in uses) or "    (none found)"
            blocks.append(
                f"## `{typ}` — field `{fld}` is NOT a member of it.\n"
                f"Real definition (reconcile to THIS shape):\n```ts\n{decl or '(not defined in game — a kit type)'}\n```\n"
                f"Member-access counts across the game (prefer an EXISTING name if one fits):\n"
                f"{member_lines}\n"
                f"    .{fld}: {_member_uses(files, fld)} use(s)  ← the referenced (wrong) name")
    for name in _EXPORT.findall(msg):
        if name in seen:
            continue
        seen.add(name)
        blocks.append(
            f"## import wants `{name}`, but no file exports it. It is likely exported under a DIFFERENT "
            f"name — read the exporting file and use its real export, do not add a stub export.")
    if not blocks:
        return ""
    return "# AUTHORITY — reconcile to what EXISTS (do not invent fields/exports)\n" + "\n\n".join(blocks)


def _contract_deterministic(run_dir, error) -> dict:
    # The safe monotone transforms only (declare export / union→enum / relax over-strict required).
    # Field-append is OFF here: a field mismatch is exactly the hallucination-prone case, routed to the
    # authority-guided LLM below instead of laundered into types.ts.
    return reconcile_types(run_dir, include_fields=False)


CONTRACT = FixClass(
    id="contract-mismatch",
    matches=_matches_contract,
    directive=_directive("contract_mismatch.txt"),
    authority=_contract_authority,
    deterministic=_contract_deterministic,
)

DEFAULT = FixClass(id="default", matches=lambda e: True)

# First match wins; `default` is last and matches everything. arg-mismatch / link / missing-behavior /
# draw / crash are not split out yet — they fall to `default` (today's generic loop) until each earns
# its own authority. Adding one = insert a FixClass before DEFAULT.
FIX_CLASSES = [CONTRACT, DEFAULT]


def classify(error) -> FixClass:
    return next(c for c in FIX_CLASSES if c.matches(error))
