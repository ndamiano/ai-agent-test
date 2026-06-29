"""compile_godot — the Godot target's spine.

Mirrors web.compiler.compile_web: reads the on-disk components, builds a self-contained Godot 4
project under <run>/godot_output, and returns the same structured pass/fail contract so the
maestro loop (the `compiles` done-condition) and final packaging are engine-agnostic.

Like web, nothing is code-generated per game: the project is the engine-neutral IR written as
game.json plus one static, pre-tested runtime (godot/runtime) that interprets it in GDScript. The
"lint" gate is therefore IR-level — assemble + cross-reference + JSON-Schema validity. Godot is
the engine that actually PLAYS combat (turn_based encounters), which web/renpy stub out.

A native binary export is best-effort (it needs the godot binary + export templates installed);
its absence never fails the build — the runnable project dir is the deliverable.
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


def compile_godot(working_dir, distribute: bool = True) -> Dict:
    """Compile the artifact to a self-contained Godot project and gate it. Always returns a
    structured pass/fail — a crash is just a compile failure with a reason."""
    from godot.ir_compiler import compile_ir
    try:
        return compile_ir(working_dir, distribute=distribute)
    except Exception as e:
        return {"ok": False, "reason": f"build error: {type(e).__name__}: {e}",
                "lint_error_count": None, "project_dir": None}
