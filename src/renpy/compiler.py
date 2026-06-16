"""compile_renpy — the spine.

Reads the artifact off disk, builds a Ren'Py project, and returns a structured
pass/fail. This is the one validator review can't fake: either the artifact
produces a launchable project that lints clean, or it doesn't.

The gate mirrors the technical-validity check the old e2e eval used: lint errors,
a distribute error, or a non-zero distribute return code fail the compile. A lint
error_count of None means no SDK was available to check — we can't gate, so it passes.
"""

import json
from pathlib import Path
from typing import Dict, Optional

from renpy.fns import build

# The artifact pieces build() consumes. brief is optional (title defaults);
# the other three are required to produce a meaningful game.
_REQUIRED = ("premise.json", "asset_manifest.json", "node_scripts.json")
_OPTIONAL = ("brief.json",)


def _load_artifact(working_dir: Path) -> Dict:
    inputs: Dict = {}
    for fname in _OPTIONAL + _REQUIRED:
        path = working_dir / fname
        if path.exists():
            inputs[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return inputs


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


def compile_renpy(working_dir, distribute: bool = True, repair: bool = False) -> Dict:
    # distribute=False lints only (fast) — used for mid-build compile checks; the
    # final delivery build packages the project.
    # repair=False (the agentic default) does NOT rewrite the agent's content; it
    # surfaces structural issues so the agent fixes them (e.g. builds a missing node).
    working_dir = Path(working_dir)

    missing = [f for f in _REQUIRED if not (working_dir / f).exists()]
    if missing:
        return {"ok": False, "reason": f"missing artifact files: {missing}",
                "lint_error_count": None, "project_dir": None}

    # build() can raise on structurally-invalid agent-authored content (e.g. a
    # character without an id). compile_renpy must always return a structured
    # pass/fail — a crash is just a compile failure with a reason.
    try:
        build_result = build(_load_artifact(working_dir), working_dir,
                             distribute=distribute, repair=repair)
    except Exception as e:
        return {"ok": False, "reason": f"build error: {type(e).__name__}: {e}",
                "lint_error_count": None, "project_dir": None}

    # Structural issues (dangling jumps, undefined characters, missing backgrounds)
    # are reported verbatim — they're the agent's actionable to-do.
    issues = build_result.get("script_issues")
    if issues:
        detail = "; ".join(f"{nid}: {prob}" for nid, prob in issues.items())
        return {"ok": False, "reason": f"script issues — {detail}",
                "lint_error_count": None, "project_dir": None}

    reason = compile_gate(build_result)
    # Surface the actual lint messages, not just the count — the agent needs to know
    # WHAT failed to fix it.
    lint_errors = build_result.get("lint", {}).get("errors") or []
    if reason and "lint error" in reason and lint_errors:
        reason = f"{reason}: " + " | ".join(lint_errors[:5])
    return {
        "ok": reason is None,
        "reason": reason,
        "lint_error_count": build_result.get("lint", {}).get("error_count"),
        "lint_errors": lint_errors,
        "dist_returncode": build_result.get("dist_returncode"),
        "dist_error": build_result.get("dist_error"),
        "project_dir": build_result.get("project_dir") or build_result.get("output_dir"),
    }
