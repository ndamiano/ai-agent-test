#!/usr/bin/env python3
# Bootstrap: add repo root to sys.path so `eval` is importable as a package,
# then add src/ so pipeline/llm_client imports work.
import sys as _sys
from pathlib import Path as _Path
_root = _Path(__file__).resolve().parent.parent
for _p in [str(_root), str(_root / "src")]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

"""
Maestro eval CLI — score and hill-climb pipeline stage prompts.

Commands:
  capture   Run pipeline once, save intermediate outputs as fixtures
  score     Score a stage (100x timebox) or full pipeline (10x timebox)
  climb     Hill-climb a stage's prompt template
  report    Print or diff saved result summaries

Examples:
  python eval/cli.py capture renpy --brief renpy_romance
  python eval/cli.py score stage renpy/premise --brief renpy_romance --time 300
  python eval/cli.py score pipeline renpy --brief renpy_romance --time 1800
  python eval/cli.py climb renpy/premise --brief renpy_romance --time-per-run 120 --iterations 10
  python eval/cli.py report eval/results/renpy/premise/renpy_romance/<run>/summary.json
  python eval/cli.py report <path_a> --compare <path_b>
"""

import argparse
import json
import sys
from pathlib import Path

import eval._paths  # noqa: F401 — sets up sys.path
from eval._paths import BRIEFS_DIR, RUBRICS_DIR


def _load_brief(name: str) -> dict:
    path = BRIEFS_DIR / f"{name}.json"
    if not path.exists():
        sys.exit(f"Brief not found: {path}\nAvailable: {[p.stem for p in BRIEFS_DIR.glob('*.json')]}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rubric(pipeline: str, stage: str) -> dict:
    path = RUBRICS_DIR / f"{pipeline}_{stage}.json"
    if not path.exists():
        available = [p.name for p in RUBRICS_DIR.glob("*.json")]
        sys.exit(f"Rubric not found: {path}\nAvailable: {available}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_capture(args):
    from eval.fixtures import capture
    brief = _load_brief(args.brief)
    capture(args.pipeline, brief, args.brief)


def cmd_score_stage(args):
    from eval.failures import print_analysis as print_failure_analysis
    from eval.fixtures import load as load_fixtures
    from eval.judge import Judge
    from eval.report import summarize, save, print_summary, print_review
    from eval.runner import StageRunner

    pipeline_name, stage_id = args.target.split("/", 1)
    rubric   = _load_rubric(pipeline_name, stage_id)
    fixtures = load_fixtures(pipeline_name, args.brief)
    runner   = StageRunner(pipeline_name, stage_id, fixtures)
    judge    = Judge()

    print(f"Scoring {args.target}  brief={args.brief}  n={args.n}")
    results = runner.run_n(args.n)
    print_failure_analysis(results)
    scored  = [
        judge.score(stage_id, r["output"], rubric) if r["ok"] and r["output"] else None
        for r in results
    ]

    if args.show_reasoning:
        for i, (r, s) in enumerate(zip(results, scored)):
            if r["ok"] and r["output"] and s:
                print_review(i + 1, r["output"], s, rubric)

    summary = summarize(results, scored, rubric)
    print_summary(summary, f"{args.target} / {args.brief}")

    if not args.no_save:
        save(pipeline_name, stage_id, args.brief, summary, run_results=results)


def cmd_score_pipeline(args):
    from eval.judge import Judge
    from eval.report import summarize, save, print_summary
    from eval.runner import PipelineEvalRunner

    brief  = _load_brief(args.brief)
    runner = PipelineEvalRunner(args.pipeline, brief)
    judge  = Judge()

    print(f"Scoring pipeline {args.pipeline}  brief={args.brief}  n={args.n}")
    results = runner.run_n(args.n)

    rubric_path = RUBRICS_DIR / f"{args.pipeline}_e2e.json"
    if rubric_path.exists():
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        scored = [
            judge.score("e2e", r.get("outputs", {}), rubric) if r["ok"] else None
            for r in results
        ]
        summary = summarize(results, scored, rubric)
    else:
        print(f"(no e2e rubric at {rubric_path.name} — reporting timing/success only)")
        n_runs = len(results)
        summary = {
            "n": n_runs,
            "success_rate": round(sum(1 for r in results if r["ok"]) / n_runs, 3) if n_runs else 0.0,
            "elapsed_mean": round(sum(r["elapsed"] for r in results) / n_runs, 1) if n_runs else 0.0,
            "elapsed_total": round(sum(r["elapsed"] for r in results), 1),
            "overall": {},
            "by_criterion": {},
        }

    print_summary(summary, f"pipeline:{args.pipeline} / {args.brief}")
    if not args.no_save:
        save(args.pipeline, "e2e", args.brief, summary)


def cmd_climb(args):
    from eval.climb import hill_climb

    pipeline_name, stage_id = args.target.split("/", 1)
    end_to_end = getattr(args, "e2e", False)
    brief_pool_names = getattr(args, "brief_pool", None) or []

    if end_to_end:
        rubric = _load_rubric(pipeline_name, "e2e")
        if brief_pool_names:
            briefs = [_load_brief(n) for n in brief_pool_names]
            brief  = None
            brief_name = "pool"
        elif args.brief:
            briefs = None
            brief  = _load_brief(args.brief)
            brief_name = args.brief
        else:
            # Auto-discover all non-character briefs
            all_briefs = [
                p.stem for p in BRIEFS_DIR.glob("*.json")
                if not p.stem.startswith("character_")
            ]
            if not all_briefs:
                sys.exit("No briefs found in eval/briefs/")
            print(f"e2e pool: {all_briefs}")
            briefs = [_load_brief(n) for n in all_briefs]
            brief  = None
            brief_name = "pool"
    else:
        if not args.brief:
            sys.exit("--brief is required for stage climbing")
        rubric = _load_rubric(pipeline_name, stage_id)
        briefs = None
        brief  = None
        brief_name = args.brief

    hill_climb(
        pipeline_name=pipeline_name,
        stage_id=stage_id,
        brief_name=brief_name,
        rubric=rubric,
        n=args.n,
        iterations=args.iterations,
        n_mutations=args.mutations,
        judge_connector=getattr(args, "judge_connector", None),
        end_to_end=end_to_end,
        brief=brief,
        briefs=briefs,
    )


def cmd_rescore(args):
    from eval.judge import Judge
    from eval.report import summarize, save, print_summary, print_diff
    from llm_clients.connector_selector import get_connector

    run_dir = Path(args.run_dir)
    outputs_path = run_dir / "outputs.json"
    summary_path = run_dir / "summary.json"

    if not outputs_path.exists():
        sys.exit(f"No outputs.json in {run_dir} — run was saved before output persistence was added.")

    outputs = json.loads(outputs_path.read_text(encoding="utf-8"))
    pipeline_name, stage_id = args.target.split("/", 1)
    rubric = _load_rubric(pipeline_name, stage_id)

    connector = get_connector(connector_type=args.connector) if args.connector else get_connector()
    judge = Judge(connector=connector)

    print(f"Rescoring {len(outputs)} outputs from {run_dir.name}"
          + (f"  connector={args.connector}" if args.connector else ""))

    scored = [
        judge.score(stage_id, r["output"], rubric) if r["ok"] and r["output"] else None
        for r in outputs
    ]
    run_results = [{"ok": r["ok"], "output": r["output"], "elapsed": 0} for r in outputs]

    if getattr(args, "show_reasoning", False):
        from eval.report import print_review
        for i, (r, s) in enumerate(zip(outputs, scored)):
            if r["ok"] and r["output"] and s:
                print_review(i + 1, r["output"], s, rubric)

    summary = summarize(run_results, scored, rubric)
    print_summary(summary, f"{args.target} rescore")

    if args.compare and summary_path.exists():
        original = json.loads(summary_path.read_text(encoding="utf-8"))
        print_diff(original, summary, "original", "rescore")

    label = f"rescore_{args.connector}" if args.connector else "rescore"
    save(pipeline_name, stage_id, run_dir.parent.name, summary, label=label)


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
        description="Maestro eval: score and hill-climb pipeline prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # capture
    p = sub.add_parser("capture", help="Run pipeline once, save intermediate outputs as fixtures")
    p.add_argument("pipeline")
    p.add_argument("--brief", required=True, help="Brief name (matches briefs/<name>.json)")

    # score
    p_score = sub.add_parser("score", help="Score a stage or full pipeline within a time budget")
    score_sub = p_score.add_subparsers(dest="score_target", required=True)

    p_stage = score_sub.add_parser("stage", help="Score a single stage (e.g. renpy/premise)")
    p_stage.add_argument("target", help="pipeline/stage_id")
    p_stage.add_argument("--brief", required=True)
    p_stage.add_argument("--n", type=int, default=5, help="Number of runs (default: 5)")
    p_stage.add_argument("--no-save", action="store_true", dest="no_save")
    p_stage.add_argument("--show-reasoning", action="store_true", dest="show_reasoning",
                         help="Print each run's output + judge reasoning for spot-checking")

    p_pipe = score_sub.add_parser("pipeline", help="Score full pipeline end-to-end")
    p_pipe.add_argument("pipeline")
    p_pipe.add_argument("--brief", required=True)
    p_pipe.add_argument("--n", type=int, default=5, help="Number of runs (default: 5)")
    p_pipe.add_argument("--no-save", action="store_true", dest="no_save")

    # climb
    p = sub.add_parser("climb", help="Hill-climb a stage's prompt template")
    p.add_argument("target", help="pipeline/stage_id (e.g. renpy/premise)")
    p.add_argument("--brief", default=None,
                   help="Brief name for stage climbing (required) or single-brief e2e mode")
    p.add_argument("--brief-pool", nargs="+", dest="brief_pool", default=None,
                   help="Multiple brief names for e2e mode; a random one is picked per run. "
                        "If omitted in e2e mode, all available briefs are used.")
    p.add_argument("--n",          type=int, default=5, help="Runs per evaluation slot (default: 5)")
    p.add_argument("--iterations", type=int, default=5, help="Hill-climb iterations (default: 5)")
    p.add_argument("--mutations",  type=int, default=5, help="Mutations per iteration (default: 5)")
    p.add_argument("--e2e", action="store_true",
                   help="Run full pipeline per trial; score final output against the <pipeline>_e2e rubric")
    p.add_argument("--judge-connector", default=None, dest="judge_connector",
                   help="Connector for judge scoring/mutation (e.g. cline). Defaults to active connector.")

    # rescore
    p = sub.add_parser("rescore", help="Re-score saved outputs with a different judge connector")
    p.add_argument("run_dir", help="Path to a result run directory containing outputs.json")
    p.add_argument("target",  help="pipeline/stage_id (e.g. renpy/node_scripts)")
    p.add_argument("--connector", default=None,
                   help="Connector type to use for judging (e.g. cline, lmstudio). Defaults to active connector.")
    p.add_argument("--compare", action="store_true",
                   help="Diff rescore against the original summary.json in the same run dir")
    p.add_argument("--show-reasoning", action="store_true", dest="show_reasoning",
                   help="Print judge reasoning for each output")

    # report
    p = sub.add_parser("report", help="Print a saved result summary")
    p.add_argument("summary", help="Path to summary.json")
    p.add_argument("--compare", default=None, help="Path to a second summary.json to diff against")

    args = parser.parse_args()

    dispatch = {
        "capture": cmd_capture,
        "score":   lambda a: cmd_score_stage(a) if a.score_target == "stage" else cmd_score_pipeline(a),
        "climb":   cmd_climb,
        "rescore": cmd_rescore,
        "report":  cmd_report,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
