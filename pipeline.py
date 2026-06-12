#!/usr/bin/env python3
"""Run a single pipeline stage manually for testing and development."""

import argparse
import json
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))


def _get_stage(pipeline, stage_id):
    for node in pipeline.nodes:
        for stage in node.stages:
            if stage.id == stage_id:
                return stage
    return None


def _list_stages(pipeline):
    from pipelines.runner import FnStage
    for node in pipeline.nodes:
        for stage in node.stages:
            kind = "fn" if isinstance(stage, FnStage) else "llm"
            inputs = getattr(stage, "inputs", [])
            print(f"  {stage.id:25s}  [{kind}]  inputs: {inputs}")


def main():
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Run a single pipeline stage for manual testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Input JSON keys must match what the stage reads via inputs.get(...).

  graph:    {"brief": {"num_endings": 4, "depth": 6, "seed": 42}}
  premise:  {"brief": {"genre": "...", "tone": "...", "setting": "...", "notes": "..."}}
  endings:  {"premise": {...}, "graph": {...}}

Tip: pipe output into the next stage's input file:
  python pipeline.py renpy -s premise inputs/brief.json > inputs/premise_out.json
""",
    )
    parser.add_argument("pipeline", help="Pipeline name (e.g. renpy, character)")
    parser.add_argument("--stage", "-s", help="Stage id to run")
    parser.add_argument("--list", "-l", action="store_true", help="List stages and their inputs")
    parser.add_argument("--runs", "-n", type=int, default=1, help="Run N times (default: 1)")
    parser.add_argument("--workdir", "-w", default=None, help="Working directory for stage (persists after run, useful for build)")
    args, remaining = parser.parse_known_args()
    args.input = remaining[0] if remaining else None

    from pipelines.registry import get_registry
    registry = get_registry()
    if args.pipeline not in registry:
        print(f"error: unknown pipeline '{args.pipeline}'. Available: {list(registry.keys())}", file=sys.stderr)
        sys.exit(1)

    defn = registry[args.pipeline]

    if args.list:
        print(f"Stages in '{args.pipeline}':")
        _list_stages(defn.pipeline)
        return

    if not args.stage:
        parser.error("--stage required (or --list to see stages)")
    if not args.input:
        parser.error("input JSON file required")

    stage = _get_stage(defn.pipeline, args.stage)
    if stage is None:
        print(f"error: unknown stage '{args.stage}'", file=sys.stderr)
        print("Run with --list to see available stages.", file=sys.stderr)
        sys.exit(1)

    from pipelines.runner import FnStage
    if not isinstance(stage, FnStage):
        print(f"error: stage '{args.stage}' is type {type(stage).__name__} — only FnStage supported", file=sys.stderr)
        sys.exit(1)

    inputs = json.loads(Path(args.input).read_text(encoding="utf-8"))
    kwargs = {}
    if stage.max_tokens is not None:
        kwargs["max_tokens"] = stage.max_tokens

    results = []
    for i in range(args.runs):
        if args.runs > 1:
            print(f"--- run {i + 1}/{args.runs} ---", file=sys.stderr)
        print(f"Running {args.pipeline}/{args.stage}...", file=sys.stderr)
        t0 = time.time()
        sys.stdout = sys.stderr
        try:
            if args.workdir:
                Path(args.workdir).mkdir(parents=True, exist_ok=True)
                result = stage.fn(inputs, Path(args.workdir), **kwargs)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    result = stage.fn(inputs, Path(tmp), **kwargs)
        finally:
            sys.stdout = sys.__stdout__
        elapsed = time.time() - t0
        print(f"Done in {elapsed:.1f}s", file=sys.stderr)
        results.append(result)

    output_data = results[0] if args.runs == 1 else results
    output_text = json.dumps(output_data, indent=2, ensure_ascii=False)

    print(output_text)


if __name__ == "__main__":
    main()
