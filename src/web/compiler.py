"""compile_web — the web target's spine.

Mirrors renpy.compiler.compile_renpy: reads the on-disk components, builds a self-contained
browser project under <run>/game_output, and returns the same structured pass/fail contract so
the maestro loop (the `compiles` done-condition) and final packaging are engine-agnostic.

Unlike Ren'Py, nothing is code-generated per game: the project is the engine-neutral IR written
as game.json plus one static, pre-tested runtime (web/runtime) that interprets it in the browser.
The "lint" gate is therefore IR-level — assemble + cross-reference + JSON-Schema validity — not an
external SDK pass.
"""

from typing import Dict, Optional


def compile_gate(build_result: Dict) -> Optional[str]:
    """Return a failure reason, or None if the built project passes the gate."""
    errs = build_result.get("ir_errors") or []
    if errs:
        return f"{len(errs)} IR error(s)"
    if build_result.get("dist_error"):
        return str(build_result["dist_error"])
    return None


def compile_web(working_dir, distribute: bool = True) -> Dict:
    """Compile the artifact to a self-contained web project and gate it. Always returns a
    structured pass/fail — a crash is just a compile failure with a reason."""
    from web.ir_compiler import compile_ir
    try:
        return compile_ir(working_dir, distribute=distribute)
    except Exception as e:
        return {"ok": False, "reason": f"build error: {type(e).__name__}: {e}",
                "lint_error_count": None, "project_dir": None}
