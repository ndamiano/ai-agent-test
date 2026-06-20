"""compile_renpy — the spine.

Reads the artifact off disk, builds a Ren'Py project, and returns a structured
pass/fail. This is the one validator review can't fake: either the artifact
produces a launchable project that lints clean, or it doesn't.

The gate mirrors the technical-validity check the old e2e eval used: lint errors,
a distribute error, or a non-zero distribute return code fail the compile. A lint
error_count of None means no SDK was available to check — we can't gate, so it passes.
"""

from typing import Dict, Optional


def compile_gate(build_result: Dict) -> Optional[str]:
    """Return a failure reason, or None if the built project passes the gate."""
    error_count = build_result.get("lint", {}).get("error_count")
    if error_count:
        return f"{error_count} lint error(s)"
    if build_result.get("dist_error"):
        return str(build_result["dist_error"])
    rc = build_result.get("dist_returncode")
    if rc not in (None, 0):
        return f"distribute returncode {rc}"
    return None


def compile_renpy(working_dir, distribute: bool = True) -> Dict:
    """Compile the artifact to a launchable Ren'Py project and gate it.

    Delegates to the IR backend (assemble_ir → ir_vn/ir_pnc → lint). Kept as the public
    entry so the `compiles` check and the final packaging step are unchanged. Always returns
    a structured pass/fail — a crash is just a compile failure with a reason.
    """
    from renpy.ir_compiler import compile_ir
    try:
        return compile_ir(working_dir, distribute=distribute)
    except Exception as e:
        return {"ok": False, "reason": f"build error: {type(e).__name__}: {e}",
                "lint_error_count": None, "project_dir": None}
