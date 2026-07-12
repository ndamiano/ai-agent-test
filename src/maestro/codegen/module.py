"""CodegenModule — the decomposed, multi-file build.

A game is a FOLDER of ES modules (`game/main.js` + system files) described by a `manifest.json` (the
code contract: each file's name + purpose + exports). The module is a list of `Check`s swept by the
base `Module`:

  - `planned`  (blocking): manifest.json exists + names main.js. Fix = author the manifest from the
                           spec's systems (one file per system, small enough to author whole).
  - `authored` (blocking): every manifest file exists + is non-empty. Fix = author the MISSING files
                           ONE per step, each against the manifest (its own purpose/exports + the
                           siblings' signatures) — bounded output, so authoring one system never
                           touches another.
  - `runs` / `plays` / `renders` / `scrolls`: the gates. Fix = rewrite exactly ONE file the model
                           names, with all files as read context and the failure — bounded output,
                           so fixing combat can't drop movement (the single-file feature-loss trap).

Every fix is a whole-body `Check.run`: one raw completion, then a write. Context is rebuilt from the
durable folder each step — no transcript memory.
"""

import json
import re
from pathlib import Path

from maestro.codegen.gates import (
    RUNTIME_DIR, entry_path, extract_code, game_files, manifest_path, read_manifest,
    run_headless, run_probe, run_render, run_scroll,
)
from maestro.modules.module import Check, Error, ErrorType, Module

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_CODE_MAX_TOKENS = 16000
_PLAN_MAX_TOKENS = 2000


def _kit_doc(spec: dict) -> str:
    doc = "kit_api_3d.md" if spec.get("mode") == "3d" else "kit_api.md"
    return (RUNTIME_DIR / doc).read_text(encoding="utf-8")


def _design_block(spec: dict) -> str:
    return f"# DESIGN SPEC\n```json\n{json.dumps(spec.get('design', spec), indent=1)}\n```"


def _infer(services, system: str, user: str, max_tokens: int) -> str:
    from llm_clients.message_builder import MessageBuilder
    msgs = MessageBuilder(system).add_user(user).build()
    resp = services.infer(msgs, [], max_tokens=max_tokens)
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def _manifest_files(run_dir) -> list:
    return [f for f in (read_manifest(run_dir).get("files") or []) if f.get("name")]


def _sibling_lines(files: list, exclude: str) -> str:
    """The importable surface of the OTHER files (name + exports + purpose) — the contract a file is
    authored against, without dumping any sibling's body."""
    out = []
    for f in files:
        if f["name"] == exclude:
            continue
        exports = ", ".join(f.get("exports") or []) or "(none)"
        out.append(f"- ./{f['name']} — {f.get('purpose', '')} — exports: {exports}")
    return "\n".join(out) or "(none)"


# ── detectors ─────────────────────────────────────────────────────────────────
def _detect_planned(check, module, context):
    files = _manifest_files(context.state.run_dir)
    if files and any(f["name"] == "main.js" for f in files):
        return []
    return [Error(type=ErrorType.BUILD, code="planned", component="game",
                  message="no manifest yet — plan the game's files from the spec")]


def _detect_authored(check, module, context):
    run_dir = context.state.run_dir
    on_disk = game_files(run_dir)
    errs = []
    for f in _manifest_files(run_dir):
        name = f["name"]
        if not (on_disk.get(name) or "").strip():
            errs.append(Error(type=ErrorType.BUILD, code="authored", component="game",
                              path=name, message=f"{name} not written yet — author it ({f.get('purpose','')})"))
    return errs


def _violations(result):
    return "; ".join(f"[{v.get('kind')}] {v.get('detail')}" for v in result.get("violations", []))


def _detect_runs(check, module, context):
    hl = run_headless(context.state.run_dir)
    if hl.get("ok"):
        return []
    return [Error(type=ErrorType.FIX, code="runs", component="game",
                  message="HEADLESS FAILED: " + json.dumps(hl))]


def _detect_plays(check, module, context):
    pr = run_probe(context.state.run_dir)
    return [] if pr.get("ok") else [Error(type=ErrorType.FIX, code="plays", component="game",
                                          message="PROBE FAILED: " + _violations(pr))]


def _detect_renders(check, module, context):
    rr = run_render(context.state.run_dir)
    return [] if rr.get("ok") else [Error(type=ErrorType.FIX, code="renders", component="game",
                                          message="RENDER FAILED: " + _violations(rr))]


def _detect_scrolls(check, module, context):
    sr = run_scroll(context.state.run_dir)
    return [] if sr.get("ok") else [Error(type=ErrorType.FIX, code="scrolls", component="game",
                                          message="CAMERA FAILED: " + _violations(sr))]


# ── fixes ─────────────────────────────────────────────────────────────────────
def _plan_fix(module, context, error, slot, services, dispatch):
    """Author the manifest: the spec's systems → a small set of files (main.js + one per system),
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
        assert any(f["name"] == "main.js" for f in files)
    except Exception:
        manifest = {"files": [{"name": "main.js", "purpose": "the whole game", "exports": ["createGame"]}]}
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
    user = "\n\n".join([
        f"# KIT API\n{_kit_doc(spec)}",
        _design_block(spec),
        f"# THIS FILE: {me['name']}\npurpose: {me.get('purpose','')}\nmust export: {', '.join(me.get('exports') or []) or '(none)'}",
        f"# OTHER FILES you may import (signatures only)\n{_sibling_lines(files, me['name'])}",
        f"Write ./{me['name']} now. Output ONLY one ```js block.",
    ])
    code = extract_code(_infer(services, system, user, _CODE_MAX_TOKENS))
    result = dispatch("write_game_file", {"code": code, "file": me["name"]})
    detail = result.get("error") or f"{result.get('chars')} chars"
    services._report(f"authored {me['name']}: {detail}")


_FILE_RE = re.compile(r"FILE:\s*([A-Za-z0-9_.-]+\.js)", re.I)


def _triage_file(infer, run_dir, failure: str) -> str:
    """Pick the ONE file to fix. For a single-file game there's no choice; otherwise a cheap call
    over the manifest SIGNATURES (never the bodies) locates the culprit — so the heavy fix call only
    ever loads one file, and a big game can't overflow the context into an empty response."""
    files = game_files(run_dir)
    if len(files) <= 1:
        return next(iter(files), "main.js")
    # A crash/stack trace usually names the file it threw in — trust that over a guess.
    named = [n for n in files if n != "main.js" and re.search(rf"\b{re.escape(n)}\b", failure)]
    if len(named) == 1:
        return named[0]
    sigs = "\n".join(f"- {f['name']}: {f.get('purpose','')} (exports: {', '.join(f.get('exports') or []) or 'none'})"
                     for f in _manifest_files(run_dir)) or "\n".join(f"- {n}" for n in files)
    system = (_PROMPTS / "triage_fix.txt").read_text(encoding="utf-8")
    user = f"# FILES\n{sigs}\n\n# FAILURE\n{failure}\n\nWhich single file must change? Reply ONLY `FILE: <name.js>`."
    m = _FILE_RE.search(infer(system, user, 200))
    return m.group(1) if (m and m.group(1) in files) else ("main.js" if "main.js" in files else next(iter(files)))


def _focused_fix(infer, spec, run_dir, target: str, failure: str, dispatch) -> dict:
    """Rewrite one file with a SMALL context: that file's body + the siblings' signatures + the
    failure. `main.js` owns config+state+wiring, so most cross-file bugs are a contract mismatch
    against it — when fixing a SYSTEM file, include main.js's body too (bounded: one extra file) so
    the fixer can see the state shape / call sites it must agree with. Bounded input AND output, so
    the model returns a complete file, not a truncated blob."""
    files = game_files(run_dir)
    body = files.get(target, "")
    siblings = _sibling_lines(_manifest_files(run_dir), target)
    parts = [f"# KIT API\n{_kit_doc(spec)}", f"# FILE TO FIX: {target}\n```js\n{body}\n```"]
    if target != "main.js" and "main.js" in files:
        parts.append(f"# main.js (owns state + calls this file — agree with it; do NOT rewrite it)\n"
                     f"```js\n{files['main.js']}\n```")
    parts += [
        f"# OTHER FILES you may import (signatures only — do NOT rewrite these)\n{siblings}",
        f"# FAILURE\n{failure}",
        f"Rewrite ./{target} completely. Output ONLY one ```js block.",
    ]
    return dispatch("write_game_file",
                    {"code": extract_code(infer(system_prompt(), "\n\n".join(parts), _CODE_MAX_TOKENS)), "file": target})


def system_prompt() -> str:
    return (_PROMPTS / "fix_file.txt").read_text(encoding="utf-8")


def _patch_file_fix(module, context, error, slot, services, dispatch):
    """Fix a gate failure in two small steps: triage (which file? — over signatures) then a focused
    rewrite of that one file. Never loads every body into one call, so a large multi-file game
    converges instead of overflowing the context into empty responses."""
    infer = lambda system, user, mt: _infer(services, system, user, mt)
    target = _triage_file(infer, context.state.run_dir, error.message)
    result = _focused_fix(infer, context.spec, context.state.run_dir, target, error.message, dispatch)
    detail = result.get("error") or f"{result.get('chars')} chars"
    services._report(f"patched {target}: {detail}")


class CodegenModule(Module):
    id = "codegen"
    layer = "engine"
    selectable = False
    component = "game"

    checks = [
        Check(code="planned", detect=_detect_planned, job="author", blocking=True, run=_plan_fix),
        Check(code="authored", detect=_detect_authored, job="author", blocking=True, run=_author_file_fix),
        Check(code="runs", detect=_detect_runs, job="fix", run=_patch_file_fix),
        Check(code="plays", detect=_detect_plays, job="fix", when_clean=True, run=_patch_file_fix),
        Check(code="renders", detect=_detect_renders, job="fix", when_clean=True, run=_patch_file_fix),
        Check(code="scrolls", detect=_detect_scrolls, job="fix", when_clean=True, run=_patch_file_fix),
    ]

    def affected_components(self):
        return ("game",)
