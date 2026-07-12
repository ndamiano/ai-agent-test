"""CodegenModule — the one module the codegen build loop runs.

It IS four checks over a single `game.js`, swept in order by the base `Module`:
  - `authored` (blocking): the file exists + is non-empty. Fix = author it from the frozen spec.
  - `runs`: the sim survives a headless smoke run. Fix = patch it from the crash detail.
  - `plays` (when_clean): the generic probe invariants hold. Fix = patch it from the violations.
  - `renders` (when_clean): the draw() path (2D) neither crashes nor paints a blank screen.
  - `scrolls` (when_clean): a world bigger than the screen is followed by a panning camera.

Each fix is a whole-body `Check.run`: one raw completion (a fenced ```js block, not a tool-call
arg), then `write_game_file`. The loop rebuilds context from durable state each step, so a patch
reads the CURRENT code + the failure the check emitted — no transcript memory.
"""

import json
from pathlib import Path

from maestro.codegen.gates import (
    RUNTIME_DIR, extract_code, game_path, run_headless, run_probe, run_render, run_scroll,
)
from maestro.modules.module import Check, Error, ErrorType, Module

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_CODE_MAX_TOKENS = 16000


def _kit_doc(spec: dict) -> str:
    doc = "kit_api_3d.md" if spec.get("mode") == "3d" else "kit_api.md"
    return (RUNTIME_DIR / doc).read_text(encoding="utf-8")


def _detect_authored(check, module, context):
    path = game_path(context.state.run_dir)
    if path.exists() and path.read_text(encoding="utf-8").strip():
        return []
    return [Error(type=ErrorType.BUILD, code="authored", component="game",
                  message="game.js not written yet — author it from the spec")]


def _detect_runs(check, module, context):
    hl = run_headless(context.state.run_dir)
    if hl.get("ok"):
        return []
    return [Error(type=ErrorType.FIX, code="runs", component="game",
                  message="HEADLESS FAILED: " + json.dumps(hl))]


def _violations(result):
    return "; ".join(f"[{v.get('kind')}] {v.get('detail')}" for v in result.get("violations", []))


def _detect_plays(check, module, context):
    pr = run_probe(context.state.run_dir)
    if pr.get("ok"):
        return []
    return [Error(type=ErrorType.FIX, code="plays", component="game",
                  message="PROBE FAILED: " + _violations(pr))]


def _detect_renders(check, module, context):
    rr = run_render(context.state.run_dir)
    if rr.get("ok"):
        return []
    return [Error(type=ErrorType.FIX, code="renders", component="game",
                  message="RENDER FAILED: " + _violations(rr))]


def _detect_scrolls(check, module, context):
    sr = run_scroll(context.state.run_dir)
    if sr.get("ok"):
        return []
    return [Error(type=ErrorType.FIX, code="scrolls", component="game",
                  message="CAMERA FAILED: " + _violations(sr))]


def _codegen_fix(module, context, error, slot, services, dispatch):
    """One authoring/patch step: infer a complete module (raw fenced block), persist it. The prompt
    is rebuilt from durable state — for a patch it carries the current code + the failing check's
    message, so the loop's minimal-context rule holds (no message history needed)."""
    from llm_clients.message_builder import MessageBuilder

    spec = context.spec
    authoring = error.code == "authored"
    system = (_PROMPTS / ("author_game.txt" if authoring else "fix_game.txt")).read_text(encoding="utf-8")
    parts = [f"# KIT API\n{_kit_doc(spec)}",
             f"# DESIGN SPEC\n```json\n{json.dumps(spec.get('design', spec), indent=1)}\n```"]
    if not authoring:
        current = game_path(context.state.run_dir).read_text(encoding="utf-8")
        parts += [f"# CURRENT game.js\n```js\n{current}\n```",
                  f"# FAILURE (fix this; keep what works)\n{error.message}"]
    parts.append("Author the complete module now. Output ONLY one ```js block.")
    msgs = MessageBuilder(system).add_user("\n\n".join(parts)).build()

    resp = services.infer(msgs, [], max_tokens=_CODE_MAX_TOKENS)
    content = ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""
    result = dispatch("write_game_file", {"code": extract_code(content)})
    verb = "authored" if authoring else "patched"
    detail = result.get("error") or f"{result.get('chars')} chars"
    services._report(f"{verb} game.js: {detail}")


class CodegenModule(Module):
    id = "codegen"
    layer = "engine"
    selectable = False
    component = "game"

    checks = [
        Check(code="authored", detect=_detect_authored, job="author", blocking=True, run=_codegen_fix),
        Check(code="runs", detect=_detect_runs, job="fix", run=_codegen_fix),
        Check(code="plays", detect=_detect_plays, job="fix", when_clean=True, run=_codegen_fix),
        Check(code="renders", detect=_detect_renders, job="fix", when_clean=True, run=_codegen_fix),
        Check(code="scrolls", detect=_detect_scrolls, job="fix", when_clean=True, run=_codegen_fix),
    ]

    def affected_components(self):
        return ("game",)
