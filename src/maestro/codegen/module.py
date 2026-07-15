"""CodegenModule — the decomposed, multi-file, typed build.

A game is a FOLDER of TypeScript modules (`game/main.ts` + system files) described by a
`manifest.json` (the code contract: each file's name + purpose + exports). Checks swept by the base:

  - `planned`  (blocking): manifest.json exists + names main.ts. Fix = author the manifest.
  - `authored` (blocking): every manifest file exists. Fix = author the MISSING files ONE per step,
                           each against the manifest (its purpose/exports + siblings' signatures).
  - `typechecks` (blocking): `tsc --noEmit` against the kit types — catches cross-file/type/contract
                           bugs (missing exports, wrong data shapes, bad arg counts) before the game
                           runs, with file:line attribution. Fix = the one file tsc blames.
  - `runs` / `plays` / `renders` / `scrolls`: the runtime gates on the bundle. Fix = the throw-site
                           file (sourcemapped stack) or a triaged pick — rewrite ONE file, bounded.

Every fix is a whole-body `Check.run`: one raw completion, then a write. Context is rebuilt from the
durable folder each step — no transcript memory.
"""

import json
import re
from pathlib import Path

from maestro.codegen.gates import (
    ENTRY_SRC, RUNTIME_DIR, extract_code, game_files, manifest_path, read_manifest,
    run_headless, run_probe, run_render, run_scroll, typecheck,
)
from maestro.modules.module import Check, Error, ErrorType, Module

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_CODE_MAX_TOKENS = 16000
_PLAN_MAX_TOKENS = 2000


def _kit_doc(spec: dict) -> str:
    doc = "kit_api_3d.md" if spec.get("mode") == "3d" else "kit_api.md"
    return (RUNTIME_DIR / doc).read_text(encoding="utf-8")


def _kit_sig_block() -> str:
    """Just the kit CALL SIGNATURES from engine.d.ts — the lines an arg-count fix needs, not the 9KB
    prose doc. Every `name(args): ret;` inside the Kit interface."""
    dts = (RUNTIME_DIR / "engine.d.ts").read_text(encoding="utf-8")
    sigs = re.findall(r"^\s{2,}(\w+\s*\([^;{]*\)\s*:\s*[^;{]+);", dts, re.M)
    return "# KIT CALL SIGNATURES (match arg count/types exactly)\n```ts\n" + "\n".join(sigs) + "\n```"


def _kit_context(spec: dict, error) -> str:
    """The kit surface a fix actually needs. A runtime gate (crash/probe/render) needs the full kit
    behavior + laws. A typecheck fix does NOT — it's a type/contract/call bug: inject nothing, unless
    an arg-count error is in play, then just the signatures. Cuts ~9KB of noise from typecheck fixes,
    where it drowns the one-line failing gate (the small-model distraction law)."""
    if getattr(error, "code", None) != "typechecks":
        return f"# KIT API\n{_kit_doc(spec)}"
    if "TS2554" in error.message or "arguments, but got" in error.message:
        return _kit_sig_block()
    return ""


def _design_block(spec: dict) -> str:
    return f"# DESIGN SPEC\n```json\n{json.dumps(spec.get('design', spec), indent=1)}\n```"


def _infer(services, system: str, user: str, max_tokens: int) -> str:
    from llm_clients.message_builder import MessageBuilder
    msgs = MessageBuilder(system).add_user(user).build()
    resp = services.infer(msgs, [], max_tokens=max_tokens)
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


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
    if files and any(f["name"] == ENTRY_SRC for f in files):
        return []
    return [Error(type=ErrorType.BUILD, code="planned", component="game",
                  message="no manifest yet — plan the game's files from the spec")]


def _is_contract(f) -> bool:
    """A shared-types file: authored FIRST, and its body is injected into every consumer so no file
    invents its own state shape (the multi-file split-brain fix)."""
    n = f.get("name", "").lower()
    p = f.get("purpose", "").lower()
    return n == "types.ts" or "shared interface" in p or "shared type" in p or \
        "type definition" in p or ("interface" in p and "state" in p)


def _detect_authored(check, module, context):
    # ONE file at a time in MANIFEST order (dependency order — the planner lists shared types first),
    # so the contract is authored before its consumers instead of alphabetically after them.
    run_dir = context.state.run_dir
    on_disk = game_files(run_dir)
    for f in _manifest_files(run_dir):
        name = f["name"]
        if not (on_disk.get(name) or "").strip():
            return [Error(type=ErrorType.BUILD, code="authored", component="game",
                          path=name, message=f"{name} not written yet — author it ({f.get('purpose','')})")]
    return []


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
    at once instead of thrashing one line at a time. Routes to the exact file, before the game runs."""
    by_file = {}
    for f, msg in typecheck(context.state.run_dir):
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


def _gate_error(code: str, label: str, detail: str, run_dir) -> Error:
    return Error(type=ErrorType.FIX, code=code, component="game",
                 path=_throw_site(detail, game_files(run_dir)),
                 message=f"{label}: {detail}")


def _detect_runs(check, module, context):
    hl = run_headless(context.state.run_dir)
    if hl.get("ok"):
        return []
    return [_gate_error("runs", "HEADLESS FAILED", json.dumps(hl), context.state.run_dir)]


def _detect_plays(check, module, context):
    pr = run_probe(context.state.run_dir)
    return [] if pr.get("ok") else [_gate_error("plays", "PROBE FAILED", _violations(pr), context.state.run_dir)]


def _detect_renders(check, module, context):
    rr = run_render(context.state.run_dir)
    return [] if rr.get("ok") else [_gate_error("renders", "RENDER FAILED", _violations(rr), context.state.run_dir)]


def _detect_scrolls(check, module, context):
    sr = run_scroll(context.state.run_dir)
    return [] if sr.get("ok") else [Error(type=ErrorType.FIX, code="scrolls", component="game",
                                          message="CAMERA FAILED: " + _violations(sr))]


# ── fixes ─────────────────────────────────────────────────────────────────────
def _plan_fix(module, context, error, slot, services, dispatch):
    """Author the manifest: the spec's systems → a small set of files (main.ts + one per system),
    each with its exports. On unparseable output, fall back to a single-file manifest so the build
    proceeds rather than thrashing on the plan."""
    spec = context.spec
    system = (_PROMPTS / "plan_game.txt").read_text(encoding="utf-8")
    user = f"{_design_block(spec)}\n\nPlan the files. Output ONLY one ```json block."
    text = _infer(services, system, user, _PLAN_MAX_TOKENS)
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    try:
        manifest = json.loads(m.group(1) if m else text)
        files = [f for f in (manifest.get("files") or []) if f.get("name")]
        assert any(f["name"] == "main.ts" for f in files)
    except Exception:
        manifest = {"files": [{"name": "main.ts", "purpose": "the whole game", "exports": ["createGame"]}]}
    manifest_path(context.state.run_dir).parent.mkdir(parents=True, exist_ok=True)
    manifest_path(context.state.run_dir).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    services._report(f"planned {len(manifest['files'])} file(s): {', '.join(f['name'] for f in manifest['files'])}")


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
    parts.append(f"# OTHER FILES you may import (signatures only)\n{_sibling_lines(run_dir, me['name'])}")
    if _is_contract(me):
        parts.append("This file is the SHARED CONTRACT for every other system listed above. Declare the "
                     "interfaces/state COMPLETE enough for ALL of them at once — every field each system "
                     "will read or write (player stats + inventory, entity/enemy fields, world/quest state, "
                     "timers, flags). A missing field forces every consumer to be re-patched, so err "
                     "toward MORE fields, not fewer.")
    parts.append(f"Write ./{me['name']} now. Output ONLY one ```js block.")
    code = extract_code(_infer(services, system, user="\n\n".join(parts), max_tokens=_CODE_MAX_TOKENS))
    result = dispatch("write_game_file", {"code": code, "file": me["name"]})
    detail = result.get("error") or f"{result.get('chars')} chars"
    services._report(f"authored {me['name']}: {detail}")


_FILE_RE = re.compile(r"FILE:\s*([A-Za-z0-9_.-]+\.ts)", re.I)


def _triage_file(infer, run_dir, failure: str, use_stack: bool = True) -> str:
    """Pick the ONE file to fix. For a single-file game there's no choice; otherwise a cheap call
    over the manifest SIGNATURES (never the bodies) locates the culprit — so the heavy fix call only
    ever loads one file, and a big game can't overflow the context into an empty response.
    `use_stack` trusts a crash STACK naming exactly one non-main file (propagated frames name main
    too); a prose human note is NOT a stack — pass False so the LLM reads the note and can pick
    main.ts (where wiring/input usually lives)."""
    files = game_files(run_dir)
    if len(files) <= 1:
        return next(iter(files), "main.ts")
    if use_stack:
        # A crash/stack trace usually names the file it threw in — trust that over a guess.
        named = [n for n in files if n != "main.ts" and re.search(rf"\b{re.escape(n)}\b", failure)]
        if len(named) == 1:
            return named[0]
    sigs = "\n".join(f"- {f['name']}: {f.get('purpose','')} (exports: {', '.join(f.get('exports') or []) or 'none'})"
                     for f in _manifest_files(run_dir)) or "\n".join(f"- {n}" for n in files)
    system = (_PROMPTS / "triage_fix.txt").read_text(encoding="utf-8")
    user = f"# FILES\n{sigs}\n\n# FAILURE\n{failure}\n\nWhich single file must change? Reply ONLY `FILE: <name.ts>`."
    m = _FILE_RE.search(infer(system, user, 200))
    return m.group(1) if (m and m.group(1) in files) else ("main.ts" if "main.ts" in files else next(iter(files)))


def _focused_fix(infer, spec, run_dir, target: str, failure: str, dispatch, include_kit: bool = True) -> dict:
    """Rewrite one file with a SMALL context: that file's body + the siblings' signatures + the
    failure. `main.ts` owns config+state+wiring, so most cross-file bugs are a contract mismatch
    against it — when fixing a SYSTEM file, include main.ts's body too (bounded: one extra file) so
    the fixer can see the state shape / call sites it must agree with. `include_kit=False` (an
    import/export link fix) drops the kit doc, which the fix doesn't need — less context, less
    truncation on a big file. Bounded input AND output, so the model returns a complete file."""
    files = game_files(run_dir)
    body = files.get(target, "")
    siblings = _sibling_lines(run_dir, target)
    parts = [f"# KIT API\n{_kit_doc(spec)}"] if include_kit else []
    parts.append(f"# FILE TO FIX: {target}\n```ts\n{body}\n```")
    # main.ts owns state+wiring; types.ts owns the shared shapes — most cross-file bugs are a
    # mismatch against one of them, and a "property missing on GameState" error reported in one file
    # is often FIXED in types.ts. Show both (read-only) so the fixer can align to them.
    for ctx_file in ("main.ts", "types.ts"):
        if ctx_file != target and ctx_file in files:
            parts.append(f"# {ctx_file} (read-only context — agree with it; do NOT rewrite it)\n"
                         f"```ts\n{files[ctx_file]}\n```")
    parts += [
        f"# OTHER FILES you may import (signatures only — do NOT rewrite these)\n{siblings}",
        f"# FAILURE\n{failure}",
        f"Rewrite ./{target} completely. Output ONLY one ```ts block.",
    ]
    return dispatch("write_game_file",
                    {"code": extract_code(infer(system_prompt(), "\n\n".join(parts), _CODE_MAX_TOKENS)), "file": target})


def system_prompt() -> str:
    return (_PROMPTS / "fix_file.txt").read_text(encoding="utf-8")


_READ_SCHEMA = {"type": "function", "function": {
    "name": "read_game_file",
    "description": "Read one game file's FULL current source. Read any sibling you need to understand "
                   "the cross-file wiring before you fix. You MUST read a file before you can edit it.",
    "parameters": {"type": "object",
                   "properties": {"file": {"type": "string", "description": "filename, e.g. world.ts"}},
                   "required": ["file"]}}}
_EDIT_SCHEMA = {"type": "function", "function": {
    "name": "edit_game_file",
    "description": "The PREFERRED fix: replace an exact snippet in a file you have read. old_string must "
                   "match the current source verbatim and be unique — copy it exactly, including "
                   "indentation. Prefer this over rewriting the whole file; make the smallest edit that "
                   "fixes the failure. If it fails it returns the current file body — re-anchor and retry.",
    "parameters": {"type": "object",
                   "properties": {"file": {"type": "string"},
                                  "old_string": {"type": "string", "description": "exact current text to replace"},
                                  "new_string": {"type": "string", "description": "replacement text"}},
                   "required": ["file", "old_string", "new_string"]}}}
_WRITE_SCHEMA = {"type": "function", "function": {
    "name": "write_game_file",
    "description": "Escape hatch: overwrite ONE file with its COMPLETE new source. Use only when an edit "
                   "cannot express the fix (a near-total rewrite). Must be a planned filename.",
    "parameters": {"type": "object",
                   "properties": {"file": {"type": "string"},
                                  "code": {"type": "string", "description": "the complete file source"}},
                   "required": ["file", "code"]}}}

_FIX_LOOP_MAX_TURNS = 8
_EDIT_FAILS_BEFORE_OVERWRITE = 3  # after N failed edits, force the overwrite escape hatch


def _read_write_loop_fix(module, context, error, slot, services, dispatch):
    """Fix a gate failure as a bounded read→write subloop: the model reads whatever siblings it needs
    (full bodies, on demand) to locate a CROSS-FILE mismatch the signatures can't show, then writes
    ONE complete file. This is the sanctioned multi-call fix shape (Check.run) — the reads live in an
    EPHEMERAL transcript confined to this one fix (the outer loop stays stateless and re-gates after).
    A single-shot fix that only sees main.ts + sibling signatures parks on bugs like 'a file assumes
    another spawns the player but none does'; reading the body exposes it. Bounded by the Services
    budget (each infer counts) + a turn cap; on cross-fix stall (escalate) the read tool is dropped so
    the fix must ACT. Context is rebuilt each turn via MessageBuilder, which dedups superseded reads."""
    from llm_clients.message_builder import MessageBuilder
    from maestro.services import parse_args, salvage_tool_call

    spec = context.spec
    run_dir = context.state.run_dir
    files = _manifest_files(run_dir)
    filelist = "\n".join(
        f"- {f['name']}: {f.get('purpose', '')} (exports: {', '.join(f.get('exports') or []) or 'none'})"
        for f in files) or "\n".join(f"- {n}" for n in game_files(run_dir))
    system = (_PROMPTS / "fix_loop.txt").read_text(encoding="utf-8")
    user = "\n\n".join(p for p in [
        _kit_context(spec, error),
        _design_block(spec),
        f"# FILES (read any you need — you are NOT shown their bodies)\n{filelist}",
        f"# FAILING GATE\n{error.message}",
        "Read whatever files you need to find the root cause, then write ONE file to fix it.",
    ] if p)
    # Ladder: read → edit (preferred, grounded, can't gut a file) → overwrite (escape hatch). Enough
    # failed edits drop edit and force a full-file overwrite; a hard outer stall (escalate) drops the
    # read tool too, so the fix must ACT rather than re-read (mirrors Services.run's escalation).
    def _schemas(edit_fails):
        if services.escalate:
            return [_WRITE_SCHEMA]
        if edit_fails >= _EDIT_FAILS_BEFORE_OVERWRITE:
            return [_READ_SCHEMA, _WRITE_SCHEMA]
        return [_READ_SCHEMA, _EDIT_SCHEMA, _WRITE_SCHEMA]

    history = [{"role": "user", "content": user}]
    wrote = mode = None
    nreads = edit_fails = 0
    for _ in range(_FIX_LOOP_MAX_TURNS):
        schemas = _schemas(edit_fails)
        msgs = MessageBuilder(system).extend(history).build()
        resp = services.infer(msgs, schemas, max_tokens=_CODE_MAX_TOKENS)
        message = (resp.get("choices") or [{}])[0].get("message", {}) or {}
        tcs = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
        if not tcs:
            salvaged = salvage_tool_call(message.get("content"), schemas)
            tcs = [salvaged] if salvaged else []
        if not tcs:
            history.append({"role": "assistant", "content": message.get("content", "") or ""})
            history.append({"role": "user",
                            "content": "Call read_game_file to inspect a file, edit_game_file to fix a "
                                       "snippet, or write_game_file for a full rewrite."})
            continue
        history.append({"role": "assistant", "content": message.get("content", "") or "", "tool_calls": tcs})
        for tc in tcs:
            name = tc["function"]["name"]
            args = parse_args(tc["function"].get("arguments"))
            if name == "write_game_file":
                result = dispatch("write_game_file",
                                  {"code": args.get("code", ""), "file": args.get("file", "main.ts")})
                if result.get("ok"):
                    wrote, mode = result.get("file"), "overwrite"
                history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
            elif name == "edit_game_file":
                result = dispatch("edit_game_file", {"file": args.get("file", "main.ts"),
                                                     "old_string": args.get("old_string", ""),
                                                     "new_string": args.get("new_string", "")})
                if result.get("ok"):
                    wrote, mode = result.get("file"), "edit"
                else:
                    edit_fails += 1
                history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
            elif name == "read_game_file":
                result = dispatch("read_game_file", {"file": args.get("file", "main.ts")})
                nreads += 1
                history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result)})
            else:
                history.append({"role": "tool", "tool_call_id": tc.get("id"),
                                "content": f"unknown tool: {name!r}"})
        if wrote:
            services._report(f"patched {wrote} via {mode} (read {nreads}, edit-miss {edit_fails})")
            return
    services._report(f"fix loop ended without a write (read {nreads}, edit-miss {edit_fails})")


def _reconcile_then_fix(module, context, error, slot, services, dispatch):
    """Typecheck failures get the DETERMINISTIC superset reconciler first: every CONTRACT-shaped error
    (missing export, a union used as a value, a field the shared types lack, a too-strict required
    field) is fixed by a SAFE, LOCAL, monotone edit to types.ts — declare/append/relax/enum-ify, never
    remove or restructure. This heals all consumers at once and converges, where an unguided LLM
    rewrite of the contract oscillates (it drops fields or restructures a type wrongly). Only the
    remaining PER-FILE logic bugs fall through to the LLM read→write loop."""
    from maestro.codegen.gates import reconcile_types
    res = reconcile_types(context.state.run_dir)
    if res.get("count"):
        summary = ", ".join(f"{k}:{v if isinstance(v, str) else '.'.join(map(str, v[:2]))}"
                            for k, v in res["changes"][:8])
        services._report(f"reconciled types.ts ({res['count']} edit(s): {summary})")
        return
    _read_write_loop_fix(module, context, error, slot, services, dispatch)


class CodegenModule(Module):
    id = "codegen"
    layer = "engine"
    selectable = False
    component = "game"

    checks = [
        Check(code="planned", detect=_detect_planned, job="author", blocking=True, run=_plan_fix),
        Check(code="authored", detect=_detect_authored, job="author", blocking=True, run=_author_file_fix),
        Check(code="typechecks", detect=_detect_typechecks, job="fix", blocking=True, run=_reconcile_then_fix),
        Check(code="runs", detect=_detect_runs, job="fix", run=_read_write_loop_fix),
        Check(code="plays", detect=_detect_plays, job="fix", when_clean=True, run=_read_write_loop_fix),
        Check(code="renders", detect=_detect_renders, job="fix", when_clean=True, run=_read_write_loop_fix),
        Check(code="scrolls", detect=_detect_scrolls, job="fix", when_clean=True, run=_read_write_loop_fix),
    ]

    def affected_components(self):
        return ("game",)
