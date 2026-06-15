import json
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

from eval._paths import RESULTS_DIR


def _distribution(values: list) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)
    n = len(s)

    def _pct(p):
        return round(s[max(0, min(n - 1, int(n * p)))], 2)

    return {
        "n": n,
        "mean":  round(statistics.mean(s), 2),
        "stdev": round(statistics.stdev(s), 2) if n > 1 else 0.0,
        "p10":   _pct(0.10),
        "p25":   _pct(0.25),
        "p75":   _pct(0.75),
        "p90":   _pct(0.90),
        "min":   round(s[0], 2),
        "max":   round(s[-1], 2),
    }


def _axes_in_order(rubric: dict) -> list:
    seen = []
    for c in rubric.get("criteria", []):
        a = c.get("axis")
        if a and a not in seen:
            seen.append(a)
    return seen


def _run_axis_mean(scored_run: dict, rubric: dict, axis: str) -> float | None:
    vals = [
        float(scored_run["scores"][c["name"]]["score"])
        for c in rubric["criteria"]
        if c.get("axis") == axis and c["name"] in scored_run.get("scores", {})
    ]
    return sum(vals) / len(vals) if vals else None


def summarize(run_results: list, scored: list, rubric: dict) -> dict:
    overall_scores = [float(s["overall"]) for s in scored if s is not None]

    by_criterion = {}
    for c in rubric.get("criteria", []):
        name = c["name"]
        vals = [
            float(s["scores"][name]["score"])
            for s in scored
            if s is not None and name in s.get("scores", {})
        ]
        by_criterion[name] = _distribution(vals)

    by_axis = {}
    for axis in _axes_in_order(rubric):
        vals = [
            m for s in scored if s is not None
            for m in [_run_axis_mean(s, rubric, axis)] if m is not None
        ]
        by_axis[axis] = _distribution(vals)

    n = len(run_results)
    ok_count = sum(1 for r in run_results if r["ok"])
    elapsed = [r["elapsed"] for r in run_results]

    summary = {
        "n": n,
        "success_rate": round(ok_count / n, 3) if n else 0.0,
        "elapsed_mean": round(statistics.mean(elapsed), 1) if elapsed else 0.0,
        "elapsed_total": round(sum(elapsed), 1),
        "overall": _distribution(overall_scores),
        "by_criterion": by_criterion,
    }
    if by_axis:
        summary["by_axis"] = by_axis
    return summary


def save(pipeline: str, stage: str, brief: str, summary: dict, label: str = "",
         run_results: list = None, scored: list = None) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"{label}_{ts}_{uuid.uuid4().hex[:6]}" if label else f"{ts}_{uuid.uuid4().hex[:6]}"
    out_dir = RESULTS_DIR / pipeline / stage / brief / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if run_results is not None:
        outputs = [{"output": r.get("output"), "ok": r.get("ok", False)} for r in run_results]
        (out_dir / "outputs.json").write_text(json.dumps(outputs, indent=2), encoding="utf-8")
    if scored is not None:
        (out_dir / "scored.json").write_text(json.dumps(scored, indent=2), encoding="utf-8")
    print(f"Saved → {out_dir.relative_to(RESULTS_DIR.parent)}/summary.json")
    return out_dir


def save_mutation(pipeline: str, stage: str, brief: str, iteration: int, idx: int,
                  mutation_text: str, summary: dict, target: str | None = None) -> Path:
    """Persist a proposed mutation's prompt text + its score so rejected ones are inspectable."""
    out_dir = RESULTS_DIR / pipeline / stage / brief / "mutations"
    out_dir.mkdir(parents=True, exist_ok=True)
    overall = summary.get("overall", {})
    p25, mean = overall.get("p25"), overall.get("mean")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"iter{iteration:02d}_mut{idx:02d}_p25-{p25}_{ts}.txt"
    header = (f"# {pipeline}/{stage}  iteration {iteration}  mutation {idx}\n"
              f"# target={target or 'weakest'}  overall p25={p25}  mean={mean}\n\n")
    path.write_text(header + mutation_text, encoding="utf-8")
    return path


def load_latest(pipeline: str, stage: str, brief: str) -> tuple | None:
    """Most recent saved run for this target with both summary and scored persisted.

    Returns (run_dir, summary, scored) or None when no resumable run exists.
    Used by hill_climb's --resume to skip re-measuring the current best prompt.
    """
    base = RESULTS_DIR / pipeline / stage / brief
    if not base.exists():
        return None
    runs = [
        d for d in base.iterdir()
        if d.is_dir() and (d / "summary.json").exists() and (d / "scored.json").exists()
    ]
    if not runs:
        return None
    latest = max(runs, key=lambda d: d.stat().st_mtime)
    summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    scored = json.loads((latest / "scored.json").read_text(encoding="utf-8"))
    return latest, summary, scored


def print_summary(summary: dict, label: str = ""):
    if label:
        print(f"\n{'='*55}")
        print(f"  {label}")
        print(f"{'='*55}")
    ov = summary.get("overall", {})
    print(
        f"n={summary['n']}  "
        f"success={summary.get('success_rate', 0)*100:.0f}%  "
        f"avg={summary.get('elapsed_mean', 0):.1f}s/run"
    )
    if ov.get("n", 0) > 0:
        print(
            f"overall:  mean={ov.get('mean','—')}  "
            f"p25={ov.get('p25','—')}  p75={ov.get('p75','—')}  "
            f"min={ov.get('min','—')}  max={ov.get('max','—')}"
        )
    if summary.get("by_axis"):
        print("by axis:")
        for name, dist in summary["by_axis"].items():
            if dist.get("n", 0) > 0:
                print(f"  {name:32s}  mean={dist.get('mean','—')}  p25={dist.get('p25','—')}  p75={dist.get('p75','—')}")
    if summary.get("by_criterion"):
        print("by criterion:")
        for name, dist in summary["by_criterion"].items():
            if dist.get("n", 0) > 0:
                print(f"  {name:32s}  mean={dist.get('mean','—')}  p25={dist.get('p25','—')}  p75={dist.get('p75','—')}")


def print_review(run_num: int, output: dict, scored: dict, rubric: dict):
    """Print one run's output + judge reasoning for quick human spot-check."""
    print(f"\n{'━'*55}")
    print(f"  Run {run_num}  — output")
    print(f"{'━'*55}")
    for key, val in output.items():
        if isinstance(val, list):
            print(f"  {key}:")
            for item in val:
                if isinstance(item, dict):
                    first_val = next(iter(item.values()), "")
                    rest = {k: v for k, v in list(item.items())[1:]}
                    print(f"    • {first_val}")
                    for k, v in rest.items():
                        print(f"        {k}: {v}")
                else:
                    print(f"    • {item}")
        else:
            print(f"  {key}: {val}")

    print(f"\n  — judge  (overall {scored.get('overall', '?')})")
    for c in rubric.get("criteria", []):
        name = c["name"]
        s = scored.get("scores", {}).get(name, {})
        reason = s.get("reasoning", "")
        cell = f"L{s['level']} ({s.get('score','?')})" if "level" in s else f"{s.get('score','?')}"
        print(f"  {name:<22} {cell:>10}   {reason}")


def print_diff(summary_a: dict, summary_b: dict, label_a: str = "A", label_b: str = "B"):
    col = 32
    print(f"\n{'Criterion':{col}}  {label_a+' p25':>9}  {label_b+' p25':>9}  {'Δp25':>7}  {label_a+' mean':>10}  {label_b+' mean':>10}  {'Δmean':>7}")
    print("-" * (col + 60))

    def _row(name, a: dict, b: dict):
        ap25  = a.get("p25",  0.0) or 0.0
        bp25  = b.get("p25",  0.0) or 0.0
        amean = a.get("mean", 0.0) or 0.0
        bmean = b.get("mean", 0.0) or 0.0
        dp25  = round(bp25  - ap25,  2)
        dmean = round(bmean - amean, 2)
        print(
            f"  {name:{col-2}}  {ap25:>9}  {bp25:>9}  "
            f"{'+' if dp25 > 0 else ''}{dp25:>6}  "
            f"{amean:>10}  {bmean:>10}  "
            f"{'+' if dmean > 0 else ''}{dmean:>6}"
        )

    _row("overall", summary_a.get("overall", {}), summary_b.get("overall", {}))
    all_criteria = sorted(
        set(summary_a.get("by_criterion", {})) | set(summary_b.get("by_criterion", {}))
    )
    for name in all_criteria:
        _row(
            name,
            summary_a["by_criterion"].get(name, {}),
            summary_b["by_criterion"].get(name, {}),
        )
