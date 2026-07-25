"""CodegenModule — the decomposed, multi-file, typed build.

A game is a FOLDER of TypeScript modules (`game/main.ts` + system files) described by a
`manifest.json` (the code contract: each file's name + purpose + exports). Checks swept by the base:

  - `interfaced`/`reviewed` (blocking): the model declares its own architecture (state fields with
                           lifetime + owner, function contracts) and reviews it for contradictions
                           before any code exists. manifest.json is derived from it.
  - `authored` (blocking): every manifest file exists. Fix = author the MISSING files ONE per step,
                           each against the manifest (its purpose/exports + siblings' signatures).
  - `typechecks` (blocking): `tsc --noEmit` against the kit types — catches cross-file/type/contract
                           bugs (missing exports, wrong data shapes, bad arg counts) before the game
                           runs, with file:line attribution. Fix = the one file tsc blames.
  - `runs` / `renders`: the runtime gates on the bundle — does it crash, does it draw.
                           Fix = grounded hunk edits landed by the read→edit subloop, bounded.

Every fix is a whole-body `Check.run`. Context is rebuilt from the durable folder each step — no
transcript memory.
"""

import json
import re
from contextlib import contextmanager
from pathlib import Path

from maestro.codegen import conform, data_files, interfaces
from maestro.codegen.gates import (
    RUNTIME_DIR,
    game_dir,
    game_files,
    read_manifest,
    run_headless,
    run_render,
    typecheck,
)
from maestro.codegen.scaffold import (
    ENTRY_HOOK,
    contract_assert_line,
    has_contract_assert,
    scheme_of,
)
from maestro.modules.module import Check, Error, ErrorType, Module

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_CODE_MAX_TOKENS = 16000
_DATA_MAX_TOKENS = 8000
_IFACE_MAX_TOKENS = 20000
_REVIEW_MAX_TOKENS = 12000


def _kit_doc(spec: dict) -> str:
    doc = "kit_api_3d.md" if spec.get("mode") == "3d" else "kit_api.md"
    text = (RUNTIME_DIR / doc).read_text(encoding="utf-8")
    # The worldgen sections (heightAt/WORLD/spawnWorld) only exist when world.ts is seeded. Injected
    # into a NON-world game they are hallucination bait — one fixer adds `heightAt(...)` per the doc
    # and the next strips it as an undefined name.
    if not spec.get("world"):
        text = re.sub(r"<!-- world -->.*?<!-- /world -->\n?", "", text, flags=re.S)
    return text


def _kit_sig_block() -> str:
    """Just the kit CALL SIGNATURES from engine.d.ts — the lines an arg-count fix needs, not the 9KB
    prose doc. Every `name(args): ret;` inside the Kit interface."""
    dts = (RUNTIME_DIR / "engine.d.ts").read_text(encoding="utf-8")
    sigs = re.findall(r"^\s{2,}(\w+\s*\([^;{]*\)\s*:\s*[^;{]+);", dts, re.M)
    return "# KIT CALL SIGNATURES (match arg count/types exactly)\n```ts\n" + "\n".join(sigs) + "\n```"


def _kit_surface_block() -> str:
    """The full ambient type surface (engine.d.ts) — the exact contract tsc checks against. For a
    fix where the model INVENTED kit API (g_fillRect, kit.mouseX), signatures alone aren't enough:
    field members (input.lookDX, entity fields) only exist here."""
    dts = (RUNTIME_DIR / "engine.d.ts").read_text(encoding="utf-8")
    return "# KIT AMBIENT TYPES (the ONLY kit surface that exists — use EXACTLY these members)\n```ts\n" + dts + "\n```"


# The model called kit API that does not exist (hallucinated names / members). TS2304/TS2552 =
# unknown name; TS2339/TS2551 on a KIT type = unknown member of the kit surface.
_KIT_TYPE_NAMES = ("'Kit'", "'DrawApi'", "'Input'", "'World'", "'Rng'", "'Camera'", "'Tilemap'")


def _is_kit_surface_error(msg: str) -> bool:
    if any(c in msg for c in ("TS2304", "TS2552")):
        return True
    return any(c in msg for c in ("TS2339", "TS2551")) and any(t in msg for t in _KIT_TYPE_NAMES)


def _kit_context(spec: dict, error) -> str:
    """The kit surface a fix actually needs. A runtime gate (crash/render) needs the full kit
    behavior + laws. A typecheck fix does NOT — it's a type/contract/call bug: inject nothing, unless
    the error shows kit MISUSE — arg-count/arg-type gets just the signatures; a hallucinated
    name/member gets the ambient d.ts (fixing API the model can't see just re-hallucinates it).
    Everything else stays lean (the small-model distraction law)."""
    if getattr(error, "code", None) != "typechecks":
        return f"# KIT API\n{_kit_doc(spec)}"
    if _is_kit_surface_error(error.message):
        return _kit_surface_block()
    if any(c in error.message for c in ("TS2554", "TS2345")) or "arguments, but got" in error.message:
        return _kit_sig_block()
    return ""


def _design_block(spec: dict) -> str:
    return f"# DESIGN SPEC\n```json\n{json.dumps(spec.get('design', spec), indent=1)}\n```"


def _seeded_block(run_dir) -> str:
    """What the PIPELINE already wrote. The planner sees only the spec otherwise, so without this a
    world game plans the terrain file it was handed — an authoring step the tools then refuse."""
    lines = ["- main.ts — the GENERATED control scaffold: config, the spec's control scheme, the "
             "frame loop. It calls your hooks in game.ts."]
    if "world.ts" in game_files(run_dir):
        lines.append("- world.ts — a GENERATED world: terrain heightfield, a laid-out town, roads, "
                     "forest and outlying sites (exports WORLD / spawnWorld / heightAt). The game is "
                     "built ON it — never plan a terrain/worldgen/map file, the world exists.")
    return "\n\n# ALREADY ON DISK — build on these, never plan them\n" + "\n".join(lines)


def _manifest_files(run_dir) -> list:
    return [f for f in (read_manifest(run_dir).get("files") or []) if f.get("name")]


# A function signature spans multiple lines (one param per line is common). Capturing only the first
# line drops every parameter, so a caller can't see the arg count/types it must pass — the #1 cause of
# unfixable arg-count oscillation. Match across newlines through the `)` + optional return type.
_FN_SIG_RE = re.compile(r"export\s+(?:async\s+)?function\s+\w+\s*\([\s\S]*?\)\s*(?::\s*[^{\n]+)?(?=\s*\{)")
_TYPE_SIG_RE = re.compile(
    r"^export\s+(?:interface|type|enum)\s+[^\n{=]+"        # export interface/type/enum X …
    r"|^export\s+(?:const|let)\s+[A-Za-z0-9_]+[^\n=]*",    # export const X: T
    re.M)


def _sibling_lines(run_dir, exclude: str) -> str:
    """The importable surface of the OTHER files — their real TYPED signatures (not just export
    names), extracted from each sibling's source, so a caller knows the exact parameters/types to
    pass. Falls back to the manifest's export names for a file not yet on disk (authoring phase)."""
    files = game_files(run_dir)
    manifest = {f["name"]: f for f in _manifest_files(run_dir)}
    out = []
    for name in sorted(set(files) | set(manifest)):
        if name == exclude:
            continue
        purpose = manifest.get(name, {}).get("purpose", "")
        src = files.get(name, "")
        sigs = [re.sub(r"\s+", " ", s).strip() for s in _FN_SIG_RE.findall(src)]
        sigs += [s.strip().rstrip("{").rstrip() for s in _TYPE_SIG_RE.findall(src)]
        if sigs:
            out.append(f"// ./{name} — {purpose}\n" + "\n".join(f"  {s}" for s in sigs[:25]))
        else:
            exports = ", ".join(manifest.get(name, {}).get("exports") or []) or "(none)"
            out.append(f"// ./{name} — {purpose} — exports: {exports}")
    return "\n".join(out) or "(none)"


# ── detectors ─────────────────────────────────────────────────────────────────
def _detect_interfaced(check, module, context):
    if (interfaces.load(context.state.run_dir) or {}).get("functions"):
        return []
    return [Error(type=ErrorType.BUILD, code="interfaced", component="game",
                  message="no architecture yet — declare this game's state fields (with their "
                          "lifetimes and owners) and its function contracts from the spec")]


def _detect_reviewed(check, module, context):
    iface = interfaces.load(context.state.run_dir)
    if not iface or iface.get("reviewed"):
        return []
    return [Error(type=ErrorType.BUILD, code="reviewed", component="game",
                  message="the architecture has not been reviewed — find and patch its "
                          "contradictions before any code is written")]


_CONFORM_HINT = {
    "OWNERSHIP": "Mutate the field in place, move the assignment into its declared owner, or amend "
                 "the declaration if the implementation names the real owner.",
    "LIFETIME": "A run-lifetime field carries progression. Rebuild nothing — mutate it in place.",
    "ELEMTYPE": "One end is wrong: either store the whole object or look the id up before use.",
    "MISSING": "Implement the declared function, or delete it from the architecture if the design "
               "no longer needs it.",
    "UNEXPORTED": "Add `export` to the implementation, or delete the function from the architecture "
                  "if nothing outside its file needs it.",
}


def _detect_conforms(check, module, context):
    """The code against the contracts the MODEL declared. Grouped by file so one fix addresses all of
    a file's violations; a MISSING function has no file to blame and rides its own error."""
    run_dir = context.state.run_dir
    iface = interfaces.load(run_dir)
    if not iface:
        return []
    by_file = {}
    for v in conform.check(iface, game_files(run_dir)):
        by_file.setdefault(v["file"], []).append(v)
    errors = []
    for f, vs in by_file.items():
        lines = "\n".join(f"  - [{v['kind']}] line {v['line']}: {v['msg']}" for v in vs)
        hints = "\n".join(f"  {_CONFORM_HINT[k]}" for k in dict.fromkeys(v["kind"] for v in vs)
                          if k in _CONFORM_HINT)
        errors.append(Error(type=ErrorType.FIX, code="conforms", component="game",
                            path=f if f != "-" else None, kind=vs[0]["kind"].lower(),
                            message=f"{f} breaks {len(vs)} contract(s) you declared:\n{lines}\n{hints}"))
    return errors


def _detect_data(check, module, context):
    """The DATA gate: a planned game must have its data files DESIGNED (even if the design is
    empty), then valid — and a valid design keeps data.ts in sync before typecheck sees it."""
    run_dir = context.state.run_dir
    if not data_files.data_manifest_path(run_dir).exists():
        return [Error(type=ErrorType.BUILD, code="data", component="game",
                      message="no data design yet — decide this game's data files from the spec")]
    violations = data_files.validate_data(run_dir)
    if violations:
        return [Error(type=ErrorType.FIX, code="data", component="game",
                      message="data files invalid:\n" + "\n".join(f"  - {v}" for v in violations))]
    data_files.generate_data_ts(run_dir)
    return []


def _is_contract(f) -> bool:
    """A shared-types file: authored FIRST, and its body is injected into every consumer so no file
    invents its own state shape (the multi-file split-brain fix)."""
    n = f.get("name", "").lower()
    p = f.get("purpose", "").lower()
    return n == "types.ts" or "shared interface" in p or "shared type" in p or \
        "type definition" in p or ("interface" in p and "state" in p)


def _authoring_order(run_dir) -> list:
    """Files in DEPENDENCY order: the shared contract first (consumers author against real types),
    game.ts LAST. The hook file is the one that depends on ALL the others — it imports and wires
    every system — so authoring it last lets it bind against its siblings' REAL on-disk signatures
    instead of guessing an API that doesn't exist yet (the source of phantom calls and missing
    imports). Everything else keeps its manifest order."""
    files = _manifest_files(run_dir)
    return sorted(files, key=lambda f: (f["name"] == ENTRY_HOOK, _is_contract(f) is False))


def _detect_authored(check, module, context):
    # ONE file at a time in dependency order (contract first, entry last), so each file is authored
    # against its dependencies' real bodies rather than a plan it will drift from.
    run_dir = context.state.run_dir
    on_disk = game_files(run_dir)
    for f in _authoring_order(run_dir):
        name = f["name"]
        if not (on_disk.get(name) or "").strip():
            return [Error(type=ErrorType.BUILD, code="authored", component="game",
                          path=name, message=f"{name} not written yet — author it ({f.get('purpose','')})")]
    return []


@contextmanager
def _with_contract_assert(run_dir, spec):
    """Hold the scaffold contract assertion in game.ts for the duration of a typecheck.

    The line is dead code whose only effect is to make a hook signature drift a LOCAL tsc error in
    game.ts, instead of an error at the GENERATED scaffold's import site that the tools refuse to
    edit. It exists for tsc and nothing else, so tsc is the only thing that ever sees it.
    """
    path = game_dir(run_dir) / ENTRY_HOOK
    src = path.read_text(encoding="utf-8") if path.exists() else ""
    added = bool(src.strip()) and not has_contract_assert(run_dir)
    if added:
        path.write_text(f"{src.rstrip()}\n{contract_assert_line(spec)}\n", encoding="utf-8")
    try:
        yield
    finally:
        if added and path.exists():
            path.write_text(src, encoding="utf-8")


_MOVER_RE = re.compile(r"kit\s*\.\s*(drive|moveTopDown3?|moveTank3|moveRelative|moveFP|walk|gridMove)\s*\(")



def _contract_block(run_dir, exclude: str) -> str:
    """Full source of already-authored contract/types files — a consumer authors against these EXACT
    shapes rather than guessing fields that won't match."""
    files = game_files(run_dir)
    out = []
    for f in _manifest_files(run_dir):
        name = f["name"]
        if name == exclude or not _is_contract(f):
            continue
        body = (files.get(name) or "").strip()
        if body:
            out.append(f"// ./{name} (import the shared types from here — use these EXACT shapes)\n{body}")
    return "\n\n".join(out)


def _detect_typechecks(check, module, context):
    """`tsc --noEmit` against the kit types — the contract gate. Errors are GROUPED BY FILE and each
    file's errors handed over together (path=file), so one rewrite fixes all of a file's type errors
    at once instead of thrashing one line at a time. Routes to the exact file, before the game runs.

    An error tsc blames on a GENERATED file (the control scaffold's import of a hook that game.ts
    fails to export, a hud whose items don't typecheck at the scaffold's call site) is RE-ATTRIBUTED
    to the hook file: the generated side is law and the tools refuse to edit it, so routing the
    error there strands the fix on a file it may not touch."""
    run_dir = context.state.run_dir
    files = game_files(run_dir)
    by_file = {}
    with _with_contract_assert(run_dir, context.spec):
        raw = typecheck(run_dir)
    for f, msg in raw:
        if f != ENTRY_HOOK and files.get(f, "").startswith("// GENERATED"):
            msg = (f"(reported in {f}, a GENERATED file whose contract is law — the real fix is "
                   f"making {ENTRY_HOOK}'s exports/signatures satisfy it) {msg}")
            f = ENTRY_HOOK
        by_file.setdefault(f, []).append(msg)
    return [Error(type=ErrorType.FIX, code="typechecks", component="game", path=f,
                  message=f"{f} has {len(msgs)} type error(s):\n" + "\n".join(f"  - {m}" for m in msgs))
            for f, msgs in by_file.items()]


def _violations(result):
    return "; ".join(f"[{v.get('kind')}] {v.get('detail')}" for v in result.get("violations", []))


_STACK_RE = re.compile(r"/game/([A-Za-z0-9_.-]+\.ts):\d+")


def _throw_site(text: str, files) -> str:
    """The file where a crash actually threw = the FIRST game file named in the stack (the deepest
    frame). A multi-file stack also names the callers, so a plain 'which files appear' guess routes
    to the wrong one; the throw site is unambiguous."""
    for m in _STACK_RE.finditer(text or ""):
        if m.group(1) in files:
            return m.group(1)
    return None


def _gate_error(code: str, label: str, detail: str, run_dir, kind: str = None) -> Error:
    return Error(type=ErrorType.FIX, code=code, component="game", kind=kind,
                 path=_throw_site(detail, game_files(run_dir)),
                 message=f"{label}: {detail}")


def _first_kind(result) -> str:
    """The dominant violation's kind — the classification hint a fix class routes on. One Error still
    carries the whole violation list in `message` (context); `kind` just names the primary failure so
    the fixer picks the right authority. Not part of Error.identity, so stall detection is unchanged."""
    return next((v.get("kind") for v in result.get("violations", []) if v.get("kind")), None)


def _detect_runs(check, module, context):
    hl = run_headless(context.state.run_dir)
    if hl.get("ok"):
        return []
    return [_gate_error("runs", "HEADLESS FAILED", json.dumps(hl), context.state.run_dir,
                        kind=hl.get("phase") or "crash")]


def _detect_renders(check, module, context):
    rr = run_render(context.state.run_dir)
    return [] if rr.get("ok") else [_gate_error("renders", "RENDER FAILED", _violations(rr),
                                                context.state.run_dir, kind=_first_kind(rr))]



# ── fixes ─────────────────────────────────────────────────────────────────────
def _hook_exports(spec: dict) -> list:
    hooks = ["createState", "init", "update", "draw", "hud"]
    if spec.get("mode") == "3d":
        hooks.remove("draw")   # a 3D game has no draw — the scene renders from entity shape tags
    return hooks


def _json_from(text: str):
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    return json.loads(m.group(1) if m else text)


_READ_SCHEMA = {"type": "function", "function": {
    "name": "read_file",
    "description": "Read one game file's current source. By default the WHOLE file — read any sibling you "
                   "need to understand the cross-file wiring before you fix. For a big file you only need a "
                   "slice of, pass offset (0-based start line) and/or limit (line count). You MUST do a full "
                   "read (no offset/limit) of a file before you can edit it — a slice does not ground an edit.",
    "parameters": {"type": "object",
                   "properties": {"file": {"type": "string", "description": "filename, e.g. world.ts"},
                                  "offset": {"type": "integer", "description": "0-based first line to read"},
                                  "limit": {"type": "integer", "description": "max lines to read from offset"}},
                   "required": ["file"]}}}
_EDIT_SCHEMA = {"type": "function", "function": {
    "name": "edit",
    "description": "THE fix tool: apply one or more exact-snippet hunks to a file you have read, "
                   "atomically — all hunks land or none do. Each hunk's old_string must match the "
                   "current source verbatim (copy it exactly, including indentation) and be unique. "
                   "Pass the SMALLEST hunks that fix the failure, and batch every hunk the fix needs "
                   "into one call. A signature change MUST ship with its call-site updates in the same "
                   "turn: same file = extra hunks in this call; other files = additional edit calls in "
                   "the same completion. To APPEND at the end of the file, pass a hunk with an EMPTY "
                   "old_string. On failure nothing is applied and the current body is returned — "
                   "re-anchor and retry.",
    "parameters": {"type": "object",
                   "properties": {"file": {"type": "string"},
                                  "edits": {"type": "array",
                                            "description": "the hunks to apply atomically",
                                            "items": {"type": "object",
                                                      "properties": {"old_string": {"type": "string", "description": "exact current text to replace; empty = append new_string at end of file"},
                                                                     "new_string": {"type": "string", "description": "replacement text"}},
                                                      "required": ["old_string", "new_string"]}}},
                   "required": ["file", "edits"]}}}
_WRITE_SCHEMA = {"type": "function", "function": {
    "name": "write",
    "description": "Create a NEW file with its complete source (authoring a planned file missing from "
                   "disk). Overwriting an existing file is refused — changes to existing code always go "
                   "through edit.",
    "parameters": {"type": "object",
                   "properties": {"file": {"type": "string"},
                                  "code": {"type": "string", "description": "the complete file source"}},
                   "required": ["file", "code"]}}}
# Runaway backstops, not budgets — the build's step cap is what bounds cost.
_FIX_LOOP_MAX_TURNS = 25
_READS_BEFORE_FORCE_ACT = 15  # reads with no write before the read tool drops and the fix must ACT

_STUB_RE = re.compile(r"placeholder|do not use|will be replaced|fill (?:this|it|in) (?:later|next)"
                      r"|actual (?:file|fix|implementation) (?:first|later)", re.I)


def _is_stub(code: str) -> bool:
    """A write that isn't a real fix — a reserve-the-file placeholder, or a body that is essentially
    empty once comments/blanks are stripped. Overwriting a real file with this bricks it."""
    real = "\n".join(l for l in code.splitlines()
                     if l.strip() and not l.strip().startswith(("//", "/*", "*")))
    return len(real.strip()) < 30 or bool(_STUB_RE.search(code))


def _fix_schemas(escalate: bool, nreads: int) -> list:
    """The fix-loop TOOLSET — read (inspect a sibling) · edit (atomic grounded hunks) · write (create a
    missing planned file). All three are real tool calls. EDIT is always offered — it is the only way to
    change an existing file, so dropping it would strand the fix. WRITE is always offered for the one
    legitimate case (a planned file absent from disk); the tool itself refuses an overwrite. READ drops
    only once THIS fix has read enough without writing (`_READS_BEFORE_FORCE_ACT`) — never on the outer
    stall: edits are grounded in reads, so a fix that cannot read can only guess anchors. Escalation's
    lever is the reasoning bump, not the toolset."""
    schemas = [_READ_SCHEMA, _EDIT_SCHEMA, _WRITE_SCHEMA]
    if nreads >= _READS_BEFORE_FORCE_ACT:
        schemas = [s for s in schemas if s is not _READ_SCHEMA]
    return schemas


class CodegenModule(Module):
    """The gate list a game is swept against. Detection only — each error's FIX is owned by the build
    driver (build_chain → build_steps), routed by `Error.code`, so the checks carry no `run`."""

    id = "codegen"
    component = "game"

    checks = [
        Check(code="interfaced", detect=_detect_interfaced, job="author", blocking=True),
        Check(code="reviewed", detect=_detect_reviewed, job="author", blocking=True),
        Check(code="data", detect=_detect_data, job="author", blocking=True),
        Check(code="authored", detect=_detect_authored, job="author", blocking=True),
        Check(code="typechecks", detect=_detect_typechecks, job="fix", blocking=True),
        Check(code="conforms", detect=_detect_conforms, job="fix"),
        Check(code="runs", detect=_detect_runs, job="fix"),
        Check(code="renders", detect=_detect_renders, job="fix", when_clean=True),
    ]

    def affected_components(self):
        return ("game",)
