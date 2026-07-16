#!/usr/bin/env python3
# Bootstrap: add repo root to sys.path so `eval` is importable as a package,
# then add src/ so llm_clients imports work.
import sys as _sys
from pathlib import Path as _Path
_root = _Path(__file__).resolve().parent.parent
for _p in [str(_root), str(_root / "src")]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

"""
Maestro eval CLI — inspect saved grading results.

Commands:
  report   Print or diff saved result summaries

Examples:
  python eval/cli.py report eval/results/<pipeline>/<stage>/<brief>/<run>/summary.json
  python eval/cli.py report <path_a> --compare <path_b>
"""

import argparse
import json
import sys
from pathlib import Path

import eval._paths  # noqa: F401 — sets up sys.path


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_report(args):
    from eval.report import print_summary, print_diff

    path = Path(args.summary)
    if not path.exists():
        sys.exit(f"Not found: {path}")
    summary = json.loads(path.read_text(encoding="utf-8"))
    print_summary(summary, str(path))

    if args.compare:
        other_path = Path(args.compare)
        if not other_path.exists():
            sys.exit(f"Not found: {other_path}")
        other = json.loads(other_path.read_text(encoding="utf-8"))
        print_diff(summary, other, path.parent.name, other_path.parent.name)


# ── parser ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Maestro eval: inspect saved grading results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("report", help="Print a saved result summary")
    p.add_argument("summary", help="Path to summary.json")
    p.add_argument("--compare", default=None, help="Path to a second summary.json to diff against")

    args = parser.parse_args()

    dispatch = {
        "report": cmd_report,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
