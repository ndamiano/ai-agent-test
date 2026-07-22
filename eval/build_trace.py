"""Per-build convergence trace from the db events log — the attribution tool for pipeline changes.

For each build: steps grouped by failing error class (check code + probe kind / TS code), wasted
steps (same class recurring after it was last cleared = a regression), elapsed. Diff two runs of the
same premise to see which error classes a change removed vs. moved.

  python -m eval.build_trace <game_id> [game_id ...]     # per-run trace
"""

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "platform.db"

_KIND = re.compile(r"\[(\w+)\]")
_TS = re.compile(r"error (TS\d+)")


def _class_of(todo0: dict) -> str:
    code = todo0.get("code") or "?"
    detail = todo0.get("detail") or ""
    m = _KIND.search(detail) or _TS.search(detail)
    return f"{code}:{m.group(1)}" if m else code


def trace(game_id: str) -> dict:
    db = sqlite3.connect(DB)
    rows = [json.loads(r[0]) for r in db.execute(
        "select payload from events where game_id=? and kind='build_step' order by rowid",
        (game_id,))]
    steps = []
    for p in rows:
        todo = p.get("todo") or []
        steps.append({"step": p.get("step"), "class": _class_of(todo[0]) if todo else "CLEAN",
                      "summary": p.get("summary", "")})
    classes = Counter(s["class"] for s in steps)
    # A class that re-appears after a DIFFERENT class ran between = churn (a fix undone or a
    # regression introduced by another fix).
    churn = 0
    seen_last = {}
    for i, s in enumerate(steps):
        if s["class"] in seen_last and seen_last[s["class"]] < i - 1:
            churn += 1
        seen_last[s["class"]] = i
    elapsed = rows[-1].get("elapsed") if rows else None
    return {"game_id": game_id, "steps": len(steps), "elapsed_s": round(elapsed or 0),
            "churn_steps": churn, "by_class": dict(classes.most_common()),
            "sequence": [f"{s['step']}:{s['class']}" for s in steps]}


if __name__ == "__main__":
    for gid in sys.argv[1:]:
        t = trace(gid)
        print(f"\n== {gid}  steps={t['steps']}  elapsed={t['elapsed_s']}s  churn={t['churn_steps']}")
        for cls, n in t["by_class"].items():
            print(f"   {n:3d}  {cls}")
        print("   " + " ".join(t["sequence"]))
