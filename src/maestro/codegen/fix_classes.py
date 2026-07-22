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

from maestro.codegen.gates import dedupe_decls, game_files, reconcile_types

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


# ── ambient-shadow ────────────────────────────────────────────────────────────
# The game imported or re-declared the AMBIENT kit types (Kit/Entity/Input come from engine.d.ts as
# globals). A local `declare namespace Kit {…}` shadows the real one with hallucinated signatures, and
# `import { Kit } from "./types"` poisons every importer — one root, errors across many files, which
# the one-file-per-fix loop churns on. Both forms are mechanically removable.
_AMBIENT_CODES = ("TS2459", "TS2708")
_KIT_IMPORT = re.compile(r"^[ \t]*import[^\n]*\bKit\b[^\n]*from[^\n]*\n", re.M)
_KIT_NAMESPACE = re.compile(r"(?:/\*\*(?:[^*]|\*(?!/))*\*/\s*)?declare\s+namespace\s+Kit\s*\{")


def _matches_ambient(error) -> bool:
    msg = error.message or ""
    return any(c in msg for c in _AMBIENT_CODES) or ("TS2307" in msg and "'./kit'" in msg)


def _strip_kit_shadow(run_dir, error) -> Optional[dict]:
    changes, count = [], 0
    for name, src in game_files(run_dir).items():
        new = _KIT_IMPORT.sub("", src)
        m = _KIT_NAMESPACE.search(new)
        if m:
            depth, j = 0, new.find("{", m.start())
            for j in range(j, len(new)):
                if new[j] == "{":
                    depth += 1
                elif new[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
            new = new[:m.start()] + new[j + 1:]
        if new != src:
            (Path(run_dir) / "game" / name).write_text(new, encoding="utf-8")
            changes.append(("strip", name))   # (kind, key) — the deterministic-pass change contract
            count += 1
    return {"changes": changes, "count": count} if count else None


AMBIENT = FixClass(
    id="ambient-shadow",
    matches=_matches_ambient,
    directive=_directive("ambient_shadow.txt"),
    deterministic=_strip_kit_shadow,
)


# ── phantom-import ────────────────────────────────────────────────────────────
# The game imports a local module that does not exist on disk — and the write allow-list (the
# manifest) means the model CANNOT create it, so the error is unresolvable by authoring: it retries
# the off-plan file forever (first prod build stalled here on `./types` in a single-file game).
# Deterministically strip the phantom import; the names it bound go undefined, which the next fix
# resolves the only way left — defining them inline.
_PHANTOM = re.compile(r"TS2307: Cannot find module '(\.\/[\w.-]+)'")


def _matches_phantom(error) -> bool:
    return bool(_PHANTOM.search(error.message or ""))


def _strip_phantom_imports(run_dir, error) -> Optional[dict]:
    from maestro.codegen.gates import game_dir

    changes, count = [], 0
    modules = set(_PHANTOM.findall(error.message or ""))
    missing = {m for m in modules
               if not (game_dir(run_dir) / (m[2:] + ".ts")).exists()}
    if not missing:
        return None
    for name, src in game_files(run_dir).items():
        new = src
        for mod in missing:
            new = re.sub(r"^[ \t]*import[^\n]*from\s+[\"']" + re.escape(mod) + r"[\"'];?[^\n]*\n",
                         "", new, flags=re.M)
        if new != src:
            (Path(run_dir) / "game" / name).write_text(new, encoding="utf-8")
            changes.append(("strip-import", name))
            count += 1
    return {"changes": changes, "count": count} if count else None


PHANTOM = FixClass(
    id="phantom-import",
    matches=_matches_phantom,
    directive=_directive("phantom_import.txt"),
    deterministic=_strip_phantom_imports,
)

# ── missing-hook ──────────────────────────────────────────────────────────────
# The scaffold imports its hooks from ./game.ts; the model authored one in a SIBLING file instead
# (createState beside the world builder is a reasonable placement). The deterministic pass bridges
# with a one-line re-export when exactly one sibling owns the hook; the LLM fallback handles the
# ambiguous/absent cases.
_HOOK_MISSING = re.compile(r"has no exported member '(createState|init|update|draw|hud)'")


def _matches_hook(error) -> bool:
    msg = error.message or ""
    if "game.ts" in msg and _HOOK_MISSING.search(msg):
        return True
    # The re-attribution marker: tsc blamed the GENERATED scaffold, so the failure IS a hook-contract
    # violation. The directive steers the model (signatures are law, fix them in game.ts); the only
    # deterministic act is the re-export bridge for a hook parked in a sibling file — signature
    # repairs stay with the model, which fixes local precise type errors reliably.
    return "GENERATED file whose contract is law" in msg


def _reexport_hooks(run_dir, error) -> Optional[dict]:
    from maestro.codegen.scaffold import reexport_hooks
    res = reexport_hooks(run_dir)
    return res if res.get("count") else None


MISSING_HOOK = FixClass(
    id="missing-hook",
    matches=_matches_hook,
    directive=_directive("missing_hook.txt"),
    deterministic=_reexport_hooks,
)


# ── duplicate-decl ────────────────────────────────────────────────────────────
# The file defines the same top-level symbol twice — two complete implementations (TS2323/TS2393,
# TS2300). Trivial under a whole-file rewrite, EDIT-HOSTILE under grounded hunks (deleting an entire
# duplicate body is one giant exact old_string — measured ~70 churned calls on one duplicate `init`).
# The deterministic pass keeps the LAST implementation (latest intent) and deletes the earlier ones.
_DUP_CODES = ("TS2323", "TS2393", "TS2300")


def _matches_duplicate(error) -> bool:
    return any(c in (error.message or "") for c in _DUP_CODES)


DUPLICATE = FixClass(
    id="duplicate-decl",
    matches=_matches_duplicate,
    directive=_directive("duplicate_decl.txt"),
    deterministic=dedupe_decls,
)

DEFAULT = FixClass(id="default", matches=lambda e: True)

# First match wins; `default` is last and matches everything. arg-mismatch / link / missing-behavior /
# draw / crash are not split out yet — they fall to `default` (today's generic loop) until each earns
# its own authority. Adding one = insert a FixClass before DEFAULT.
FIX_CLASSES = [AMBIENT, PHANTOM, MISSING_HOOK, DUPLICATE, CONTRACT, DEFAULT]


def classify(error) -> FixClass:
    return next(c for c in FIX_CLASSES if c.matches(error))
