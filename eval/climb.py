import sys as _sys
import pathlib as _pl
_src = _pl.Path(__file__).resolve().parent.parent / "src"
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))

import logging
import time
from contextlib import contextmanager
from pathlib import Path

from eval.failures import print_analysis as print_failure_analysis
from eval.fixtures import load as load_fixtures
from eval.judge import Judge
from eval.report import summarize, save, print_summary, print_diff
from eval.runner import StageRunner, PipelineEvalRunner

logger = logging.getLogger(__name__)


def _find_prompt_path(pipeline_name: str, stage_id: str) -> Path:
    from pipelines.registry import get_registry
    from pipelines.runner import LLMStage, FnStage

    pipeline = get_registry()[pipeline_name].pipeline
    for node in pipeline.nodes:
        for stage in node.stages:
            if stage.id == stage_id:
                if isinstance(stage, LLMStage):
                    return pipeline.prompts_dir / stage.prompt_template
                if isinstance(stage, FnStage) and stage.prompt_file:
                    return pipeline.prompts_dir / stage.prompt_file
                raise TypeError(
                    f"Stage {stage_id!r} is a FnStage without prompt_file — not hill-climbable. "
                    "Set prompt_file on the FnStage to enable climbing."
                )
    raise ValueError(f"Stage {stage_id!r} not found in {pipeline_name!r}")


@contextmanager
def _prompt_override(prompt_path: Path, new_text: str):
    """Temporarily replace a prompt template for one evaluation run."""
    original = prompt_path.read_text(encoding="utf-8")
    prompt_path.write_text(new_text, encoding="utf-8")
    try:
        yield
    finally:
        prompt_path.write_text(original, encoding="utf-8")


def _score_run(results: list, stage_id: str, rubric: dict, judge: Judge, e2e: bool = False) -> list:
    scored = []
    for r in results:
        if r["ok"]:
            payload = r.get("outputs", {}) if e2e else r.get("output")
            if payload:
                scored.append(judge.score(stage_id, payload, rubric))
                continue
        scored.append(None)
    return scored


def hill_climb(
    pipeline_name: str,
    stage_id: str,
    brief_name: str,
    rubric: dict,
    n: int = 5,
    iterations: int = 5,
    n_mutations: int = 5,
    judge_connector: str | None = None,
    end_to_end: bool = False,
    brief: dict | None = None,
    briefs: list | None = None,
) -> tuple:
    """
    Hill-climb the prompt for a single LLM stage.

    Each iteration:
      1. Propose n_mutations rewrites targeting the weakest criterion
      2. Score each with n runs
      3. Accept the best mutation if it improves p25 vs current best
      4. Write the final best prompt back to the template file

    Returns (final_prompt_text, final_summary).

    judge_connector: connector type for scoring/mutation ("cline", "openrouter", etc).
      Defaults to the configured connector (same as pipeline). Pass "cline" to grade
      with Claude while the pipeline runs on the local model.

    end_to_end: run the full pipeline per trial instead of just the target stage.
      Scores the final combined output against the rubric (typically renpy_e2e.json).
      Requires brief or briefs to be passed in.

    briefs: list of brief dicts for e2e mode. A random brief is chosen per run.
      Takes precedence over brief when provided.
    """
    from llm_clients.connector_selector import get_connector as _get_connector
    judge = Judge(_get_connector(judge_connector) if judge_connector else None)
    prompt_path = _find_prompt_path(pipeline_name, stage_id)

    if end_to_end:
        brief_pool = briefs or ([brief] if brief else None)
        if not brief_pool:
            raise ValueError("end_to_end=True requires brief or briefs")
        runner = PipelineEvalRunner(pipeline_name, brief_pool)
        score_stage_id = "e2e"
    else:
        fixtures = load_fixtures(pipeline_name, brief_name)
        runner = StageRunner(pipeline_name, stage_id, fixtures)
        score_stage_id = stage_id

    print(f"\n[baseline] {pipeline_name}/{stage_id}  n={n}{'  (end-to-end)' if end_to_end else ''}")
    baseline_t0 = time.monotonic()
    baseline_results = runner.run_n(n)
    baseline_elapsed = time.monotonic() - baseline_t0
    baseline_run_mean = sum(r["elapsed"] for r in baseline_results) / len(baseline_results) if baseline_results else 0.0
    print(f"  baseline done  {baseline_elapsed:.0f}s total  run mean={baseline_run_mean:.1f}s")
    print_failure_analysis(baseline_results)
    baseline_scored  = _score_run(baseline_results, score_stage_id, rubric, judge, e2e=end_to_end)
    baseline_summary = summarize(baseline_results, baseline_scored, rubric)
    print_summary(baseline_summary, "Baseline")
    save(pipeline_name, stage_id, brief_name, baseline_summary, label="baseline",
         run_results=baseline_results)

    current_prompt  = prompt_path.read_text(encoding="utf-8")
    current_summary = baseline_summary
    current_scored  = baseline_scored

    for i in range(iterations):
        iter_t0 = time.monotonic()
        print(f"\n{'─'*55}")
        print(f"Iteration {i+1}/{iterations}  ({n_mutations} mutations × {n} runs each)")

        # Sync file to current best before proposing/testing mutations
        prompt_path.write_text(current_prompt, encoding="utf-8")

        # Propose mutations
        mutations = []
        for m in range(n_mutations):
            print(f"  proposing mutation {m+1}/{n_mutations}...")
            try:
                mutations.append(judge.propose_mutation(current_prompt, current_summary, rubric, current_scored))
            except Exception as e:
                logger.warning(f"Mutation proposal {m+1} failed: {e}")

        if not mutations:
            print("  no mutations produced — stopping early")
            break

        # Evaluate mutations, pick best by p25
        best_mutation = None
        best_summary  = None
        best_scored   = None

        for j, mutation in enumerate(mutations):
            mut_t0 = time.monotonic()
            print(f"\n  [mutation {j+1}/{len(mutations)}]")
            with _prompt_override(prompt_path, mutation):
                results = runner.run_n(n)
            mut_elapsed = time.monotonic() - mut_t0
            run_times = [r["elapsed"] for r in results]
            run_mean = sum(run_times) / len(run_times) if run_times else 0.0
            print(f"  mutation done  {mut_elapsed:.0f}s total  run mean={run_mean:.1f}s")
            print_failure_analysis(results)
            scored  = _score_run(results, score_stage_id, rubric, judge, e2e=end_to_end)
            summary = summarize(results, scored, rubric)

            candidate_p25 = summary.get("overall", {}).get("p25", 0.0) or 0.0
            best_p25      = best_summary.get("overall", {}).get("p25", 0.0) if best_summary else -1.0
            if candidate_p25 > best_p25:
                best_mutation = mutation
                best_summary  = summary
                best_scored   = scored

        iter_elapsed = time.monotonic() - iter_t0
        current_p25 = current_summary.get("overall", {}).get("p25", 0.0) or 0.0
        best_p25    = best_summary.get("overall", {}).get("p25", 0.0) if best_summary else 0.0

        print(f"\n  iteration done  {iter_elapsed:.0f}s  best candidate p25={best_p25:.2f}  current p25={current_p25:.2f}")

        if best_summary and best_p25 > current_p25:
            delta = best_p25 - current_p25
            print(f"  accepted  Δp25={delta:+.2f}")
            print_diff(current_summary, best_summary, "before", "after")
            current_prompt  = best_mutation
            current_summary = best_summary
            current_scored  = best_scored
            save(pipeline_name, stage_id, brief_name, current_summary, label=f"iter{i+1:02d}_accepted",
                 run_results=results)
        else:
            print("  rejected — no improvement")

    # Write final best prompt back
    prompt_path.write_text(current_prompt, encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"Hill climb done.  Final p25={current_summary.get('overall', {}).get('p25', '—')}")
    print_diff(baseline_summary, current_summary, "start", "final")
    print(f"\nPrompt written to: {prompt_path}")

    return current_prompt, current_summary
