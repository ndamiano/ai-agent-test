"""Ren'Py SDK lint runner + lint-line→component attribution.

Engine-side helpers shared by every compile backend (the IR VN/PnC compilers and, for now,
the legacy text stitch). Kept separate from the stitch modules so the compilers don't depend
on each other: given a built project dir + SDK path, run lint and map each error line back to
the node/room/place that owns it (via the `label <id>:` / `screen <id>:` spans the compilers
emit), so the agent fixes one scene instead of churning across all of them.
"""

import platform
import re
import subprocess
from typing import List, Tuple

_MARKER_RE = re.compile(r'^\s*(?:label|screen)\s+(\w+)')


def _run_renpy_lint(output_dir: str, sdk_path: str) -> str:
    import os
    sdk_path = os.path.abspath(sdk_path)
    renpy_bin = os.path.join(sdk_path, "renpy.exe" if platform.system() == "Windows" else "renpy.sh")
    if not os.path.exists(renpy_bin):
        return ""
    try:
        proc = subprocess.run(
            [renpy_bin, os.path.abspath(output_dir), "lint"],
            capture_output=True, text=True, timeout=120,
        )
        return proc.stdout + proc.stderr
    except Exception as e:
        print(f"    [build]  lint error: {e}")
        return ""


def _parse_lint_errors(lint_output: str) -> List[Tuple[int, str]]:
    results = []
    lines = lint_output.splitlines()
    for i, line in enumerate(lines):
        m = re.search(r'\.rpy:(\d+)|[Ll]ine\s+(\d+)', line)
        if m:
            lineno = int(m.group(1) or m.group(2))
            context = " ".join(lines[i:i + 3]).strip()
            results.append((lineno, context))
    return results


def node_line_ranges(full_script: str, node_ids: List[str]) -> List[Tuple[int, int, str]]:
    """Map each node's `label <id>:` to its [start, end] line span in the stitched script,
    so a lint error on a line can be attributed to the node that owns it."""
    starts = []
    for i, ln in enumerate(full_script.split("\n"), 1):
        m = re.match(r'\s*label\s+(\w+)\s*:', ln)
        if m and m.group(1) in node_ids:
            starts.append((i, m.group(1)))
    starts.sort()
    ranges = []
    for idx, (start, sid) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else 10 ** 9
        ranges.append((start, end, sid))
    return ranges


def pnc_line_ranges(full_script: str, room_ids: List[str],
                    node_ids: List[str]) -> List[Tuple[int, int, str]]:
    """Map each script line span to the room/place (or dialogue node) that owns it. A place owns
    several blocks: its `screen <rid>`, driver `label <rid>`, `label _loop_<rid>`, and one
    `label hs_<rid>_<hid>` per hotspot — all keyed back to <rid>. Node labels map to the node id."""
    rids = sorted(room_ids, key=len, reverse=True)
    markers = []
    for i, ln in enumerate(full_script.split("\n"), 1):
        m = _MARKER_RE.match(ln)
        if not m:
            continue
        name = m.group(1)
        owner = next((rid for rid in rids
                      if name == rid or name == f"_loop_{rid}"
                      or name.startswith(f"hs_{rid}_")), None)
        if owner is None and name in node_ids:
            owner = name
        markers.append((i, owner))
    markers.sort()
    ranges = []
    for idx, (start, owner) in enumerate(markers):
        if owner is None:
            continue
        end = markers[idx + 1][0] - 1 if idx + 1 < len(markers) else 10 ** 9
        ranges.append((start, end, owner))
    return ranges


def _node_for_line(lineno: int, ranges: List[Tuple[int, int, str]]):
    for start, end, sid in ranges:
        if start <= lineno <= end:
            return sid
    return None


def run_final_lint(output_dir: str, sdk_path: str, node_ranges=None) -> dict:
    lint_output = _run_renpy_lint(output_dir, sdk_path)
    errors = _parse_lint_errors(lint_output) if lint_output else []
    formatted = []
    for lineno, ctx in errors[:10]:
        sid = _node_for_line(lineno, node_ranges) if node_ranges else None
        formatted.append(f"[{sid}] {ctx}" if sid else ctx)
    return {
        "error_count": len(errors),
        "errors": formatted,
    }
