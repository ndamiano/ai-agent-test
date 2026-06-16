#!/usr/bin/env python3
# Bootstrap: add repo root to sys.path so `eval` is importable as a package,
# then add src/ so renpy/llm_client imports work.
import sys as _sys
from pathlib import Path as _Path
_root = _Path(__file__).resolve().parent.parent
for _p in [str(_root), str(_root / "src")]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

"""
Maestro eval CLI — grade a finished artifact.

Commands:
  score game   Score an already-built game directory against the e2e rubric
  report       Print or diff saved result summaries

Examples:
  python eval/cli.py score game renpy eval/games/renpy_romance_<id>
  python eval/cli.py report eval/results/renpy/e2e/<brief>/<run>/summary.json
  python eval/cli.py report <path_a> --compare <path_b>

Hill-climbing of generation prompts is reintroduced in a later phase; only
finished-artifact grading is wired here.
"""

import argparse
import json
import sys
from pathlib import Path

import eval._paths  # noqa: F401 — sets up sys.path
from eval._paths import RUBRICS_DIR


def _load_rubric(pipeline: str, stage: str) -> dict:
    path = RUBRICS_DIR / f"{pipeline}_{stage}.json"
    if not path.exists():
        available = [p.name for p in RUBRICS_DIR.glob("*.json")]
        sys.exit(f"Rubric not found: {path}\nAvailable: {available}")
    return json.loads(path.read_text(encoding="utf-8"))


def _make_judge(connector_name: str | None = None):
    """A Judge bound to the named connector, or the active one when None."""
    from eval.judge import Judge
    from llm_clients.connector_selector import get_connector
    return Judge(connector=get_connector(connector_name) if connector_name else None)


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_score_game(args):
    from renpy.e2e_view import e2e_artifact
    from eval.report import summarize, print_summary, print_review, save

    game_dir = Path(args.game_dir)
    artifact = e2e_artifact(game_dir)
    if not artifact:
        sys.exit(f"No gradable artifact found under {game_dir} "
                 f"(expected a built game with game/script.rpy).")

    rubric = (json.loads(Path(args.rubric).read_text(encoding="utf-8"))
              if args.rubric else _load_rubric(args.pipeline, "e2e"))
    judge = _make_judge(args.connector)

    print(f"Scoring game {game_dir.name}  connector={args.connector or 'default'}")
    scored = judge.score("e2e", artifact, rubric)
    if scored is None:
        sys.exit("Judge returned no score — check the connector/model (JSON mode support?).")

    label = f"game:{game_dir.name}  ({args.connector or 'default'})"
    if args.show_reasoning:
        print_review(1, {"artifact": f"({game_dir.name})"}, scored, rubric)
    summary = summarize([{"ok": True, "elapsed": 0.0}], [scored], rubric)
    print_summary(summary, label)
    if not args.no_save:
        save(args.pipeline, "e2e", game_dir.name, summary, scored=[scored])


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
        description="Maestro eval: grade a finished artifact",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_score = sub.add_parser("score", help="Grade a finished artifact")
    score_sub = p_score.add_subparsers(dest="score_target", required=True)

    p_game = score_sub.add_parser("game",
                                  help="Score an already-built game directory against the e2e rubric")
    p_game.add_argument("pipeline")
    p_game.add_argument("game_dir", help="A built game dir (e.g. eval/games/renpy_romance_<id>)")
    p_game.add_argument("--connector", default=None,
                        help="Judge connector: cline | openrouter | lmstudio (default: configured)")
    p_game.add_argument("--rubric", default=None,
                        help="Path to a rubric JSON (default: rubrics/<pipeline>_e2e.json)")
    p_game.add_argument("--show-reasoning", action="store_true", dest="show_reasoning",
                        help="Print the judge's per-criterion reasoning")
    p_game.add_argument("--no-save", action="store_true", dest="no_save")

    p = sub.add_parser("report", help="Print a saved result summary")
    p.add_argument("summary", help="Path to summary.json")
    p.add_argument("--compare", default=None, help="Path to a second summary.json to diff against")

    args = parser.parse_args()

    dispatch = {
        "score":  lambda a: {"game": cmd_score_game}[a.score_target](a),
        "report": cmd_report,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
