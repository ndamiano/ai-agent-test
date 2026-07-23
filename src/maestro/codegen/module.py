"""CodegenModule — the decomposed, multi-file, typed build.

A game is a FOLDER of TypeScript modules (`game/main.ts` + system files) described by a
`manifest.json` (the code contract: each file's name + purpose + exports). Checks swept by the base:

  - `planned`  (blocking): manifest.json exists + names main.ts. Fix = author the manifest.
  - `authored` (blocking): every manifest file exists. Fix = author the MISSING files ONE per step,
                           each against the manifest (its purpose/exports + siblings' signatures).
  - `typechecks` (blocking): `tsc --noEmit` against the kit types — catches cross-file/type/contract
                           bugs (missing exports, wrong data shapes, bad arg counts) before the game
                           runs, with file:line attribution. Fix = the one file tsc blames.
  - `runs` / `plays` / `renders` / `scrolls`: the runtime gates on the bundle. Fix = grounded hunk
                           edits landed by the read→edit subloop, bounded.

Every fix is a whole-body `Check.run`. Context is rebuilt from the durable folder each step — no
transcript memory.
"""

import json
import re
from pathlib import Path

from llm_clients.message_builder import MessageBuilder
from maestro.codegen import data_files
from maestro.codegen.fix_classes import (
    classify,
    strip_dead_creategame,
    strip_unplanned_imports,
)
from maestro.codegen.gates import (
    RUNTIME_DIR,
    extract_code,
    game_files,
    manifest_path,
    read_manifest,
    run_headless,
    run_probe,
    run_render,
    run_scroll,
    typecheck,
)
from maestro.codegen.scaffold import (
    ENTRY_HOOK,
    contract_assert_line,
    has_contract_assert,
    scheme_of,
)
from maestro.modules.module import Check, Error, ErrorType, Module
from maestro.services import parse_args, salvage_tool_call

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_CODE_MAX_TOKENS = 16000
_PLAN_MAX_TOKENS = 2000
_DATA_MAX_TOKENS = 8000


def _kit_doc(spec: dict) -> str:
    doc = "kit_api_3d.md" if spec.get("mode") == "3d" else "kit_api.md"
    text = (RUNTIME_DIR / doc).read_text(encoding="utf-8")
    # The worldgen sections (heightAt/WORLD/spawnWorld) only exist when world.ts is seeded. Injected
    # into a NON-world game they are hallucination bait: a measured build oscillated 10+ steps because
    # a probe fix obediently added `heightAt(...)` (per the doc) and the typecheck fix then stripped
    # the undefined name — two fixers undoing each other.
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
    """The kit surface a fix actually needs. A runtime gate (crash/probe/render) needs the full kit
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


def _infer(services, system: str, user: str, max_tokens: int) -> str:
    msgs = MessageBuilder(system).add_user(user).build()
    resp = services.infer(msgs, [], max_tokens=max_tokens)
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def _author_via_write(services, system: str, user: str, dispatch, file: str, max_tokens: int) -> dict:
    """Author ONE file THROUGH the write tool — the same real tool call the fix loop uses, not a raw
    fenced-block completion scraped by extract_code. The tool boundary is what stops the model treating
    the block as a scratchpad (chatter comments, a second "rewritten" copy of a function): the payload
    is a single `code` arg for one named file, not free-form markdown. Falls back to salvaging a tool
    call, then to a fenced block, from the content — a local model that ignores the tool still lands."""
    msgs = MessageBuilder(system).add_user(user).build()
    # The model thinks in-content (the reasoning knob is a no-op on it) and a long think can eat the
    # whole token budget, truncating the tool call mid-arg → unparseable → no code. That's a per-call
    # coin flip, so one immediate retry usually lands — without it the whole outer step (re-detect,
    # rebuilt context) is spent to do the same retry.
    for _ in range(2):
        resp = services.infer(msgs, [_WRITE_SCHEMA], max_tokens=max_tokens)
        message = (resp.get("choices") or [{}])[0].get("message", {}) or {}
        content = message.get("content", "") or ""
        tcs = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name") == "write"]
        if not tcs:
            salvaged = salvage_tool_call(content, [_WRITE_SCHEMA])
            tcs = [salvaged] if salvaged else []
        code = parse_args(tcs[0]["function"].get("arguments")).get("code", "") if tcs else extract_code(content)
        if code.strip():
            break
    return dispatch("write", {"code": code, "file": file})


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
def _detect_planned(check, module, context):
    files = _manifest_files(context.state.run_dir)
    if files and any(f["name"] == ENTRY_HOOK for f in files):
        return []
    return [Error(type=ErrorType.BUILD, code="planned", component="game",
                  message="no manifest yet — plan the game's files from the spec")]


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


def _detect_contracted(check, module, context):
    """A scaffolded game.ts must carry the contract assertion — the line that makes every hook
    signature drift a LOCAL tsc error in game.ts (the shape the model fixes reliably) instead of an
    error at the GENERATED scaffold's import site. Its absence is silent to tsc, so this gate
    demands it; the MODEL appends it via the edit subloop (a prompting fix — the pipeline never
    edits game.ts)."""
    run_dir = context.state.run_dir
    if not (game_files(run_dir).get(ENTRY_HOOK) or "").strip() or has_contract_assert(run_dir):
        return []
    line = contract_assert_line(context.spec)
    return [Error(type=ErrorType.FIX, code="contracted", component="game", path=ENTRY_HOOK,
                  message=f"{ENTRY_HOOK} is missing the scaffold contract assertion — read the file, "
                          f"then append EXACTLY this line at the end (ONE edit hunk with an EMPTY "
                          f"old_string appends):\n{line}")]


_MOVER_RE = re.compile(r"kit\s*\.\s*(drive|moveTopDown3?|moveTank3|moveRelative|moveFP|walk|gridMove)\s*\(")


def _detect_single_mover(check, module, context):
    """Movement is wired ONCE, in the GENERATED main.ts. A model file calling an input-driven kit
    mover AGAIN double-moves the player (2x speed) or fights the scaffold — one measured build
    shipped a knight at double speed (game.ts ran moveTopDown3 on top of the scaffold's kit.drive)
    and no runtime gate can see it (the probe only checks that movement exists). Static and cheap,
    so it runs between typecheck and the runtime gates."""
    run_dir = context.state.run_dir
    errors = []
    for name, src in game_files(run_dir).items():
        if src.lstrip().startswith("// GENERATED"):
            continue
        for m in _MOVER_RE.finditer(src):
            window = src[m.end():m.end() + 150]
            if not re.search(r"\binput\b", window.split(";")[0]):
                continue
            ln = src[:m.start()].count("\n") + 1
            errors.append(Error(
                type=ErrorType.FIX, code="single_mover", component="game", path=name,
                message=f"{name} line {ln}: `kit.{m.group(1)}(..., input, ...)` — but this is a "
                        f"SCAFFOLDED game: the GENERATED main.ts already applies the spec's control "
                        f"scheme to state.player EVERY frame, before your update runs. A second "
                        f"input-driven movement call double-moves the player (or fights the "
                        f"scaffold's move). DELETE the whole call statement; keep any bounds/ground "
                        f"clamps that run after it. Movement speed is tuned via state.player.speed, "
                        f"never by re-wiring input."))
            break   # one per file — the fix strips every occurrence anyway
    return errors


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
    error there strands the fix (measured: a capped run spent ~130 calls on main.ts errors it was
    forbidden from touching)."""
    run_dir = context.state.run_dir
    files = game_files(run_dir)
    by_file = {}
    for f, msg in typecheck(run_dir):
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


def _detect_plays(check, module, context):
    # The spec's control scheme rides along so the probe can hold a movement scheme to actual
    # displacement (dead_movement), not just "some key changed something" — and the spec's whole
    # `controls` map rides too, so the probe can press every registered action (dead_action) and
    # flag spec-bound keys nothing registered (unbound_control). ALL entries are passed; the probe
    # owns the explicit movement/mouse skip lists (it already knows the scheme's keys).
    design = (context.spec or {}).get("design") or {}
    controls = design.get("controls")
    pr = run_probe(context.state.run_dir, scheme=scheme_of(context.spec),
                   control_keys=controls if isinstance(controls, dict) and controls else None)
    return [] if pr.get("ok") else [_gate_error("plays", "PROBE FAILED", _violations(pr),
                                                context.state.run_dir, kind=_first_kind(pr))]


def _detect_renders(check, module, context):
    rr = run_render(context.state.run_dir)
    return [] if rr.get("ok") else [_gate_error("renders", "RENDER FAILED", _violations(rr),
                                                context.state.run_dir, kind=_first_kind(rr))]


def _detect_scrolls(check, module, context):
    sr = run_scroll(context.state.run_dir)
    return [] if sr.get("ok") else [Error(type=ErrorType.FIX, code="scrolls", component="game",
                                          kind=_first_kind(sr) or "no_camera",
                                          message="CAMERA FAILED: " + _violations(sr))]


# ── fixes ─────────────────────────────────────────────────────────────────────
def _hook_exports(spec: dict) -> list:
    hooks = ["createState", "init", "update", "draw", "hud"]
    if spec.get("mode") == "3d":
        hooks.remove("draw")   # a 3D game has no draw — the scene renders from entity shape tags
    return hooks


def _plan_fix(module, context, error, slot, services, dispatch):
    """Author the manifest: the spec's systems → a small set of files (the entry-hook game.ts + one
    per system), each with its exports. A file the pipeline already GENERATED (main.ts's control
    scaffold, a world game's world.ts) is never planned — the model can't author or edit it, so
    listing it would strand an authoring step. On unparseable output, fall back to a single-file
    manifest so the build proceeds rather than thrashing on the plan."""
    spec = context.spec
    run_dir = context.state.run_dir
    system = (_PROMPTS / "plan_game.txt").read_text(encoding="utf-8")
    user = f"{_design_block(spec)}{_seeded_block(run_dir)}\n\nPlan the files. Output ONLY one ```json block."
    text = _infer(services, system, user, _PLAN_MAX_TOKENS)
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    generated = {n for n, src in game_files(run_dir).items() if src.lstrip().startswith("// GENERATED")}
    try:
        manifest = json.loads(m.group(1) if m else text)
        files = [f for f in (manifest.get("files") or []) if f.get("name")]
        files = [f for f in files if f["name"] not in generated]
        assert any(f["name"] == ENTRY_HOOK for f in files)
        manifest = {"files": files}
    except Exception:
        manifest = {"files": [{"name": ENTRY_HOOK,
                               "purpose": "the whole game behind the scaffold hooks",
                               "exports": _hook_exports(spec)}]}
    manifest_path(context.state.run_dir).parent.mkdir(parents=True, exist_ok=True)
    manifest_path(context.state.run_dir).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    services._report(f"planned {len(manifest['files'])} file(s): {', '.join(f['name'] for f in manifest['files'])}")


def _json_from(text: str):
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    return json.loads(m.group(1) if m else text)


def _design_data_fix(module, context, error, slot, services, dispatch):
    """One-shot design of the game's data files from the frozen spec (the analog of _plan_fix).
    Unparseable output falls back to the EMPTY design so the build proceeds; a dataset the
    validator still rejects after landing is dropped wholesale (write_design)."""
    run_dir = context.state.run_dir
    system = (_PROMPTS / "design_data.txt").read_text(encoding="utf-8")
    filelist = "\n".join(f"- {f['name']}: {f.get('purpose', '')}" for f in _manifest_files(run_dir)) \
        or "(none planned yet)"
    user = (f"{_design_block(context.spec)}\n\n# PLANNED FILES\n{filelist}\n\n"
            "Design the data files. Output ONLY one ```json block.")
    text = _infer(services, system, user, _DATA_MAX_TOKENS)
    try:
        design = _json_from(text)
        datasets = design.get("datasets")
        assert isinstance(datasets, list)
    except Exception:
        datasets = []
    dropped = data_files.write_design(run_dir, datasets)
    data_files.generate_data_ts(run_dir)
    kept = [d["name"] for d in data_files.read_data_manifest(run_dir).get("datasets", [])]
    msg = f"designed {len(kept)} dataset(s): {', '.join(kept) or '(none)'}"
    if dropped:
        msg += f" — dropped invalid: {', '.join(dropped)}"
    services._report(msg)


def _fix_data_rows_fix(module, context, error, slot, services, dispatch):
    """One-shot rewrite of ONE offending dataset's rows from the violation list. The manifest's
    declared types are law — the rows reconcile to them, never the other way."""
    run_dir = context.state.run_dir
    manifest = data_files.read_data_manifest(run_dir)
    violations = data_files.validate_data(run_dir)
    declared = {d.get("name") for d in (manifest.get("datasets") or []) if isinstance(d, dict)}
    offending = data_files.offending_datasets(violations, declared)
    if not offending:
        services._report("data invalid but no dataset attributable — regenerate the design")
        return
    target = offending[0]
    rows_path = data_files.data_dir(run_dir) / f"{target}.json"
    raw = rows_path.read_text(encoding="utf-8") if rows_path.exists() else "[]"
    system = (_PROMPTS / "fix_data.txt").read_text(encoding="utf-8")
    user = "\n\n".join([
        f"# DATA MANIFEST (declared fields — the types are law)\n```json\n"
        f"{json.dumps(manifest, indent=1, ensure_ascii=False)}\n```",
        f"# CURRENT ROWS: data/{target}.json\n```json\n{raw}\n```",
        "# VIOLATIONS\n" + "\n".join(f"- {v}" for v in violations),
        f"Fix dataset '{target}'. Output ONLY one ```json block: the corrected rows array.",
    ])
    text = _infer(services, system, user, _DATA_MAX_TOKENS)
    try:
        rows = _json_from(text)
        assert isinstance(rows, list)
    except Exception:
        services._report(f"data fix for '{target}' returned no rows array")
        return
    rows_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    remaining = data_files.validate_data(run_dir)
    if not remaining:
        data_files.generate_data_ts(run_dir)
    services._report(f"rewrote data/{target}.json ({len(rows)} rows), "
                     f"{len(remaining)} violation(s) remain")


def _data_fix(module, context, error, slot, services, dispatch):
    if error.type is ErrorType.BUILD:
        _design_data_fix(module, context, error, slot, services, dispatch)
    else:
        _fix_data_rows_fix(module, context, error, slot, services, dispatch)


def _author_file_fix(module, context, error, slot, services, dispatch):
    """Author ONE file (error.path) against the manifest: its own purpose + exports, the siblings'
    importable signatures, the kit, and the design. Bounded output = one file."""
    spec = context.spec
    run_dir = context.state.run_dir
    files = _manifest_files(run_dir)
    me = next((f for f in files if f["name"] == error.path), {"name": error.path, "purpose": "", "exports": []})
    system = (_PROMPTS / "author_file.txt").read_text(encoding="utf-8")
    parts = [
        f"# KIT API\n{_kit_doc(spec)}",
        _design_block(spec),
        f"# THIS FILE: {me['name']}\npurpose: {me.get('purpose','')}\nmust export: {', '.join(me.get('exports') or []) or '(none)'}",
    ]
    contract = _contract_block(run_dir, me["name"])
    if contract:
        parts.append("# SHARED TYPES — author against these EXACT shapes; do NOT invent fields that "
                     f"aren't here (import the types you need from their file)\n{contract}")
    data = data_files.data_summary(run_dir)
    if data:
        parts.append(data)
    parts.append(f"# OTHER FILES you may import (signatures only)\n{_sibling_lines(run_dir, me['name'])}")
    if _is_contract(me):
        parts.append((_PROMPTS / "contract_rules.txt").read_text(encoding="utf-8"))
    parts.append(f"Call write to create ./{me['name']} now — the whole file as the `code` arg.")
    result = _author_via_write(services, system, "\n\n".join(parts), dispatch, me["name"], _CODE_MAX_TOKENS)
    detail = result.get("error") or f"{result.get('chars')} chars"
    if not result.get("error"):
        if strip_unplanned_imports(run_dir, {f["name"] for f in files}):
            detail += " (stripped unplanned import)"
        if strip_dead_creategame(run_dir):
            detail += " (stripped dead createGame)"
    services._report(f"authored {me['name']}: {detail}")


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
_FIX_LOOP_MAX_TURNS = 8
_READS_BEFORE_FORCE_ACT = 4  # after N reads with no write, drop read so the fix must ACT (kills the
                             # read-thrash where a big/corrupt file eats every turn and none writes

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
    stall: edits are grounded in reads, and an escalated fix that cannot read can only guess anchors
    (measured: a stalled target spiraled — blind edit-misses, nothing ever landed, stall persisted).
    Escalation's lever is the Services reasoning bump, not the toolset."""
    schemas = [_READ_SCHEMA, _EDIT_SCHEMA, _WRITE_SCHEMA]
    if nreads >= _READS_BEFORE_FORCE_ACT:
        schemas = [s for s in schemas if s is not _READ_SCHEMA]
    return schemas


def _read_write_loop_fix(module, context, error, slot, services, dispatch, fix_class=None):
    """Fix a gate failure as a bounded read→edit subloop: the model reads whatever siblings it needs
    (full bodies, on demand) to locate a CROSS-FILE mismatch the signatures can't show, then lands
    atomic hunk edits — every action a real tool call (read/edit/write, write create-only). This is the
    sanctioned multi-call fix shape (Check.run) — the reads live in an EPHEMERAL transcript confined to
    this one fix (the outer loop stays stateless and re-gates after). A single-shot fix that only sees
    main.ts + sibling signatures parks on bugs like 'a file assumes another spawns the player but none
    does'; reading the body exposes it. Bounded by the Services budget (each infer counts) + a turn cap;
    on cross-fix stall (escalate) the READ tool is dropped so the fix must ACT on the grounding it has
    (and Services escalates reasoning for the rest of the target). Context is rebuilt each turn via
    MessageBuilder, which dedups superseded reads.

    `fix_class` (see fix_classes.py) supplies the error-class-specific steering: an AUTHORITY block (the
    on-disk context that biases toward the correct root cause, e.g. a type's real members) and a
    DIRECTIVE (root-cause framing). `default`/None adds neither — the generic loop, unchanged."""
    spec = context.spec
    run_dir = context.state.run_dir
    files = _manifest_files(run_dir)
    filelist = "\n".join(
        f"- {f['name']}: {f.get('purpose', '')} (exports: {', '.join(f.get('exports') or []) or 'none'})"
        for f in files) or "\n".join(f"- {n}" for n in game_files(run_dir))
    # The kit doc is 9-16KB of STATIC reference. It rides in the system prompt (never dropped, never
    # counted against MESSAGE_BUDGET_CHARS) — not the user turn, where it would crowd out the model's
    # own file reads and force the budget to demolish them, starving the fix of the bodies it just read.
    system = (_PROMPTS / "fix_loop.txt").read_text(encoding="utf-8")
    kit = _kit_context(spec, error)
    if kit:
        system = f"{system}\n\n{kit}"
    contract = ((_PROMPTS / "contract_invariant.txt").read_text(encoding="utf-8")
                if any(_is_contract(f) for f in files) else "")
    authority = fix_class.authority(spec, run_dir, error) if (fix_class and fix_class.authority) else ""
    directive = fix_class.directive if fix_class else ""
    user = "\n\n".join(p for p in [
        _design_block(spec),
        contract,
        f"# FILES (read any you need — you are NOT shown their bodies)\n{filelist}",
        data_files.data_summary(run_dir),
        f"# FAILING GATE\n{error.message}",
        authority,
        directive,
        "Read whatever files you need to find the root cause, then fix it with edit — the smallest "
        "hunks that fix the failure, every needed hunk (definition + call sites) in the same "
        "completion.",
    ] if p)
    history = [{"role": "user", "content": user}]
    wrote = mode = None
    nreads = edit_fails = 0   # edit_fails is reporting-only — misses no longer change the toolset
    for _ in range(_FIX_LOOP_MAX_TURNS):
        schemas = _fix_schemas(services.escalate, nreads)
        msgs = MessageBuilder(system).extend(history).build()
        # reasoning OFF: local models honor only on/off, and the fix is a bounded read→act loop where
        # thinking-on burns the token budget on reasoning and starves the tool call. The reads do the
        # diagnosis empirically.
        resp = services.infer(msgs, schemas, reasoning="none", max_tokens=_CODE_MAX_TOKENS)
        message = (resp.get("choices") or [{}])[0].get("message", {}) or {}
        content = message.get("content", "") or ""
        tcs = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
        if not tcs:
            salvaged = salvage_tool_call(content, schemas)
            tcs = [salvaged] if salvaged else []
        # The model thinks in-content (the reasoning knob is a no-op on it); a hard fix can emit a
        # 16K-token analysis with no tool call. Appended verbatim it dominates the char budget and
        # MessageBuilder drops the file reads to fit — starving the very turn that must act. Keep the
        # TAIL (the conclusion lives at the end); the full text was never load-bearing.
        if len(content) > 2000:
            content = "[…analysis truncated…]\n" + content[-2000:]
        if not tcs:
            history.append({"role": "assistant", "content": content})
            history.append({"role": "user",
                            "content": "Call a tool: read_file to inspect a file, edit to apply "
                                       "hunks, or write to create a missing file."})
            continue
        history.append({"role": "assistant", "content": content, "tool_calls": tcs})
        for tc in tcs:
            name = tc["function"]["name"]
            args = parse_args(tc["function"].get("arguments"))
            if name == "write":
                code = args.get("code", "")
                if _is_stub(code):
                    result = {"ok": False, "error": "that is a stub/placeholder, not the complete file — "
                              "resend the ENTIRE working source in `code`."}
                else:
                    result = dispatch("write", {"code": code, "file": args.get("file", "main.ts")})
                    if result.get("ok"):
                        wrote, mode = result.get("file"), "write"
                history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
            elif name == "edit":
                result = dispatch("edit", {"file": args.get("file", "main.ts"),
                                           "edits": args.get("edits") or []})
                if result.get("ok"):
                    wrote, mode = result.get("file"), "edit"
                else:
                    edit_fails += 1
                history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
            elif name == "read_file":
                read_args = {"file": args.get("file", "main.ts")}
                if args.get("offset") is not None:
                    read_args["offset"] = args["offset"]
                if args.get("limit") is not None:
                    read_args["limit"] = args["limit"]
                result = dispatch("read_file", read_args)
                nreads += 1
                history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
            else:
                history.append({"role": "tool", "tool_call_id": tc.get("id"),
                                "content": f"unknown tool: {name!r}"})
        if wrote:
            services._report(f"patched {wrote} via {mode} (read {nreads}, edit-miss {edit_fails})")
            return
    services._report(f"fix loop ended without a write (read {nreads}, edit-miss {edit_fails})")


def dispatch_fix(module, context, error, slot, services, dispatch):
    """Route a gate failure to its FIX CLASS (fix_classes.classify), then run that class's fix: its
    optional DETERMINISTIC pre-pass first (a safe bulk collapse — e.g. the contract reconciler heals
    all consumers at once and converges where an unguided LLM rewrite oscillates), and only if that
    made no change, the read→write loop steered by the class's authority + directive. The class is the
    codegen analog of IR's per-check owner: it decides WHICH authority resolves the error, so a probe
    `dead_controls` and a tsc contract error can share the same machinery without the gate knowing.
    `default` matches everything and adds no steering, so any unclassified failure is exactly today's
    generic loop — never worse."""
    cls = classify(error)
    if cls.deterministic is not None:
        res = cls.deterministic(context.state.run_dir, error) or {}
        if res.get("count"):
            summary = ", ".join(f"{k}:{v if isinstance(v, str) else '.'.join(map(str, v[:2]))}"
                                for k, v in res.get("changes", [])[:8])
            services._report(f"[{cls.id}] deterministic pass ({res['count']} edit(s): {summary})")
            return
    _read_write_loop_fix(module, context, error, slot, services, dispatch, fix_class=cls)


class CodegenModule(Module):
    id = "codegen"
    layer = "engine"
    selectable = False
    component = "game"

    checks = [
        Check(code="planned", detect=_detect_planned, job="author", blocking=True, run=_plan_fix),
        Check(code="data", detect=_detect_data, job="author", blocking=True, run=_data_fix),
        Check(code="authored", detect=_detect_authored, job="author", blocking=True, run=_author_file_fix),
        Check(code="contracted", detect=_detect_contracted, job="fix", blocking=True, run=dispatch_fix),
        Check(code="typechecks", detect=_detect_typechecks, job="fix", blocking=True, run=dispatch_fix),
        Check(code="single_mover", detect=_detect_single_mover, job="fix", run=dispatch_fix),
        Check(code="runs", detect=_detect_runs, job="fix", run=dispatch_fix),
        Check(code="plays", detect=_detect_plays, job="fix", when_clean=True, run=dispatch_fix),
        Check(code="renders", detect=_detect_renders, job="fix", when_clean=True, run=dispatch_fix),
        Check(code="scrolls", detect=_detect_scrolls, job="fix", when_clean=True, run=dispatch_fix),
    ]

    def affected_components(self):
        return ("game",)
