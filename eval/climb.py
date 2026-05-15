import sys as _sys, pathlib as _pl
_src = _pl.Path(__file__).resolve().parent.parent / "src"
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from eval.fixtures import load as load_fixtures
from eval.judge import Judge
from eval.report import summarize, save, print_summary, print_diff
from eval.runner import StageRunner

logger = logging.getLogger(__name__)


def _find_prompt_path(pipeline_name: str, stage_id: str) -> Path:
    from pipelines.registry import get_registry
    from pipelines.runner import LLMStage

    pipeline = get_registry()[pipeline_name].pipeline
    for node in pipeline.nodes:
        for stage in node.stages:
            if stage.id == stage_id:
                if not isinstance(stage, LLMStage):
                    raise TypeError(f"Stage {stage_id!r} is a FnStage — no prompt to hill-climb")
                return pipeline.prompts_dir / stage.prompt_template
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


def _score_run(results: list, stage_id: str, rubric: dict, judge: Judge) -> list:
    scored = []
    for r in results:
        if r["ok"] and r["output"]:
            scored.append(judge.score(stage_id, r["output"], rubric))
        else:
            scored.append(None)
    return scored


def hill_climb(
    pipeline_name: str,
    stage_id: str,
    brief_name: str,
    rubric: dict,
    time_per_run: float = 120.0,
    per_run_timeout: Optional[float] = None,
    iterations: int = 10,
    n_mutations: int = 3,
) -> tuple:
    """
    Hill-climb the prompt for a single LLM stage.

    Each iteration:
      1. Propose n_mutations rewrites targeting the weakest criterion
      2. Score each with time_per_run / n_mutations second timebox
      3. Accept the best mutation if it improves p25 vs current best
      4. Write the final best prompt back to the template file

    Returns (final_prompt_text, final_summary).
    """
    judge = Judge()
    prompt_path = _find_prompt_path(pipeline_name, stage_id)
    fixtures = load_fixtures(pipeline_name, brief_name)
    runner = StageRunner(pipeline_name, stage_id, fixtures)

    timeout_str = f"  per-run-timeout={per_run_timeout:.0f}s" if per_run_timeout else ""
    print(f"\n[baseline] {pipeline_name}/{stage_id}  budget={time_per_run:.0f}s{timeout_str}")
    baseline_results = runner.run_timed(time_per_run, per_run_timeout=per_run_timeout)
    baseline_scored  = _score_run(baseline_results, stage_id, rubric, judge)
    baseline_summary = summarize(baseline_results, baseline_scored, rubric)
    print_summary(baseline_summary, "Baseline")
    save(pipeline_name, stage_id, brief_name, baseline_summary, label="baseline")

    current_prompt  = prompt_path.read_text(encoding="utf-8")
    current_summary = baseline_summary

    mutation_budget = max(20.0, time_per_run / n_mutations)

    for i in range(iterations):
        print(f"\n{'─'*55}")
        print(f"Iteration {i+1}/{iterations}  (mutation budget={mutation_budget:.0f}s each)")

        # Sync file to current best before proposing/testing mutations
        prompt_path.write_text(current_prompt, encoding="utf-8")

        # Propose mutations
        mutations = []
        for m in range(n_mutations):
            print(f"  proposing mutation {m+1}/{n_mutations}...")
            try:
                mutations.append(judge.propose_mutation(current_prompt, current_summary, rubric))
            except Exception as e:
                logger.warning(f"Mutation proposal {m+1} failed: {e}")

        if not mutations:
            print("  no mutations produced — stopping early")
            break

        # Evaluate mutations, pick best by p25
        best_mutation = None
        best_summary  = None

        for j, mutation in enumerate(mutations):
            print(f"\n  [mutation {j+1}/{len(mutations)}]  running for {mutation_budget:.0f}s")
            with _prompt_override(prompt_path, mutation):
                results = runner.run_timed(mutation_budget, per_run_timeout=per_run_timeout)
            scored  = _score_run(results, stage_id, rubric, judge)
            summary = summarize(results, scored, rubric)

            candidate_p25 = summary.get("overall", {}).get("p25", 0.0) or 0.0
            best_p25      = best_summary.get("overall", {}).get("p25", 0.0) if best_summary else -1.0
            if candidate_p25 > best_p25:
                best_mutation = mutation
                best_summary  = summary

        current_p25 = current_summary.get("overall", {}).get("p25", 0.0) or 0.0
        best_p25    = best_summary.get("overall", {}).get("p25", 0.0) if best_summary else 0.0

        print(f"\n  best candidate p25={best_p25:.2f}  current p25={current_p25:.2f}")

        if best_summary and best_p25 > current_p25:
            delta = best_p25 - current_p25
            print(f"  accepted  Δp25={delta:+.2f}")
            print_diff(current_summary, best_summary, "before", "after")
            current_prompt  = best_mutation
            current_summary = best_summary
            save(pipeline_name, stage_id, brief_name, current_summary, label=f"iter{i+1:02d}_accepted")
        else:
            print("  rejected — no improvement")

    # Write final best prompt back
    prompt_path.write_text(current_prompt, encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"Hill climb done.  Final p25={current_summary.get('overall', {}).get('p25', '—')}")
    print_diff(baseline_summary, current_summary, "start", "final")
    print(f"\nPrompt written to: {prompt_path}")

    return current_prompt, current_summary
