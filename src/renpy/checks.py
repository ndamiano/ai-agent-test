"""Ren'Py story-structure checks.

The generic checks (exists/count/distinct/each_has/refs_resolve/compiles) can't express
"is this a real story" — that needs parsing the .rpy in node_scripts.scripts. These
Ren'Py-specific checks register into maestro.validate so a spec can demand a substantial
game (reachable scenes, branching choices, multi-beat nodes, every character used).

Each check fn has the validate signature: (artifact, check, run_dir) -> (ok, detail).
Call register_all() once at startup (run.py / app.py).
"""

import re
from typing import Dict, List, Optional, Tuple

from maestro.validate import register_check

CheckResult = Tuple[bool, Optional[str]]

# Words that look like a speaker but are Ren'Py statements, not characters.
_KEYWORDS = {"scene", "show", "hide", "jump", "return", "menu", "call", "pause", "play",
             "stop", "queue", "voice", "nvl", "window", "image", "define", "transform",
             "init", "python", "label", "with", "if", "elif", "else", "while"}

_JUMP_RE = re.compile(r'\bjump\s+(\w+)')
_CALL_RE = re.compile(r'\bcall\s+(\w+)')
_MENU_RE = re.compile(r'^\s*menu\s*:', re.MULTILINE)
_SPEAKER_RE = re.compile(r'^[ \t]*(\w+)\s+"', re.MULTILINE)
_QUOTED_RE = re.compile(r'^[ \t]*(?:\w+\s+)?"', re.MULTILINE)  # narration or "char \"...\""


def _node_scripts(artifact: Dict) -> Tuple[List[str], Dict[str, str]]:
    ns = artifact.get("node_scripts", {}) or {}
    return ns.get("node_ids", []) or [], ns.get("scripts", {}) or {}


def _targets(script: str) -> set:
    return set(_JUMP_RE.findall(script)) | set(_CALL_RE.findall(script))


def _reachable(node_ids: List[str], scripts: Dict[str, str]) -> set:
    if not node_ids:
        return set()
    start = node_ids[0]
    reachable, frontier = {start}, [start]
    while frontier:
        cur = frontier.pop()
        for tgt in _targets(scripts.get(cur, "")):
            if tgt in scripts and tgt not in reachable:
                reachable.add(tgt)
                frontier.append(tgt)
    return reachable


def node_view(artifact: Dict) -> Dict:
    """Compact map of the current node graph for the build agent's context — so it uses
    real ids and repoints dangling jumps instead of inventing renamed nodes. The `edges`
    give it the exact `jump <target>` text to fix via edit_node."""
    node_ids, scripts = _node_scripts(artifact)
    reachable = _reachable(node_ids, scripts)
    return {
        "node_ids": node_ids,
        "edges": {nid: sorted(_targets(scripts.get(nid, ""))) for nid in node_ids},
        "reachable": sorted(reachable),
        "unreachable": [n for n in node_ids if n not in reachable],
        "line_counts": {nid: len(_QUOTED_RE.findall(scripts.get(nid, ""))) for nid in node_ids},
    }


def check_reachable_from_start(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    node_ids, scripts = _node_scripts(artifact)
    if not node_ids:
        return False, "no nodes to reach"
    start = node_ids[0]
    reachable, frontier = {start}, [start]
    while frontier:
        cur = frontier.pop()
        for tgt in _targets(scripts.get(cur, "")):
            if tgt in scripts and tgt not in reachable:
                reachable.add(tgt)
                frontier.append(tgt)
    orphans = [n for n in node_ids if n not in reachable]
    if orphans:
        return False, f"nodes unreachable from '{start}': {orphans[:5]}"
    return True, None


def check_min_branches(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    _, scripts = _node_scripts(artifact)
    n = sum(len(_MENU_RE.findall(s)) for s in scripts.values())
    need = check.get("min", 1)
    if n < need:
        return False, f"only {n} menu choice block(s), need {need} — add player choices"
    return True, None


def check_each_node_min_lines(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    node_ids, scripts = _node_scripts(artifact)
    need = check.get("min", 3)
    thin = []
    for nid in node_ids:
        lines = len(_QUOTED_RE.findall(scripts.get(nid, "")))
        if lines < need:
            thin.append(f"{nid} ({lines})")
    if thin:
        return False, f"nodes with < {need} dialogue/narration lines: {thin[:5]}"
    return True, None


def check_all_characters_speak(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    chars = {c.get("id") for c in artifact.get("premise", {}).get("characters", []) if c.get("id")}
    if not chars:
        return False, "premise has no characters"
    _, scripts = _node_scripts(artifact)
    spoke = set()
    for s in scripts.values():
        spoke |= {sp for sp in _SPEAKER_RE.findall(s) if sp not in _KEYWORDS}
    silent = sorted(chars - spoke)
    if silent:
        return False, f"characters who never speak: {silent} — give them lines"
    return True, None


_CHECKS = {
    "reachable_from_start": check_reachable_from_start,
    "min_branches": check_min_branches,
    "each_node_min_lines": check_each_node_min_lines,
    "all_characters_speak": check_all_characters_speak,
}


def register_all() -> None:
    for name, fn in _CHECKS.items():
        register_check(name, fn)
    # Point-and-click structure checks (rooms_reachable / items_obtainable / ...). Harmless
    # to register alongside the VN checks — a spec only references the ones its genre uses.
    from renpy.pnc_checks import register_all as register_pnc_checks
    register_pnc_checks()
    # Guarantee a Ren'Py spec's baseline done-conditions exist regardless of what the
    # proposer drafted (it has dropped the structure/quality checks). enforce_baseline picks
    # the genre's baseline from spec["genre"]. Idempotent.
    from maestro.spec_tools import register_spec_normalizer
    from renpy.spec_baseline import enforce_baseline
    register_spec_normalizer(enforce_baseline)
