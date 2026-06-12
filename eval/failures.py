"""
Failure analysis for eval runs.

Each result dict may contain a "log" key with captured stdout from the stage
runner. We classify failures by pattern-matching that log, then surface fix hints.
"""

import re
from collections import Counter

# Ordered — first match wins
_PATTERNS = [
    ("timeout",        r""),                          # set by runner, not log
    ("empty_content",  r"column 1 \(char 0\)|content:\s*\n|empty content|streaming produced empty"),
    ("repetition",     r"repetiti(ve|on)|looping"),
    ("json_error",     r"invalid json|jsondecode|failed to get valid json|return only the json"),
    ("validation",     r"validation failed|missing required keys|output is not a dict"),
    ("llm_error",      r"llm error:|status [45]\d\d|rate limit|connection"),
    ("format_error",   r"does not support response_format|does not support json"),
]

_FIX_HINTS = {
    "timeout": """\
Stage exceeded per-run timeout.
  • Reduce pipeline_max_tokens in settings.json (current default: 4096)
  • Check for repetition loops — model may be spiralling before detection
  • Increase --per-run-timeout if the model is just slow on this hardware""",

    "repetition": """\
Model entered a repetition loop before the detector caught it.
  • Increase frequency_penalty in settings.json (try 0.7–1.0; default is 0.5)
  • Shorten the prompt — less context = less surface area for loops
  • Reduce pipeline_max_tokens so loops terminate faster""",

    "json_error": """\
Stage returning invalid JSON after all correction attempts.
  • Add an output skeleton to the prompt — show the exact JSON structure first,
    then field descriptions. Model fills a skeleton rather than constructing from scratch.
  • Check prompt isn't asking for multiple JSON objects or markdown prose around the JSON
  • Verify the stage schema matches what the prompt actually asks the model to produce""",

    "validation": """\
Stage output missing required fields (passed JSON parse but failed schema check).
  • List required field names explicitly in the prompt, not just in descriptions
  • Add the output skeleton — model needs to see the exact keys expected
  • Check whether the schema was recently updated and the prompt wasn't""",

    "llm_error": """\
LLM connector error (network, auth, rate limit, or bad endpoint).
  • Verify settings.json base_url and model name are correct
  • Check that your local model server (LMStudio/Ollama) is running
  • Inspect logs/llm_requests.log and logs/llm_responses.log for the raw error
  • If rate-limited: add a delay or reduce parallelism""",

    "empty_content": """\
Model returned empty content (blank response, not a JSON parse failure on real text).
  • Most common with local models: context window exceeded, model server crash, or OOM
  • Check model server logs for errors during this run
  • Reduce pipeline_max_tokens in settings.json — model may be hitting its context limit
  • connector._streaming_works auto-disables streaming after empty result, but resets each eval run
  • Try setting streaming: false in settings or switching to a non-streaming endpoint""",

    "format_error": """\
Model endpoint does not support response_format / json_schema.
  • Retry-without-format fallback fires automatically, but adds latency and reduces reliability
  • Switch to a model that supports structured output, or use model_category: "small" in settings
    (small mode uses manual JSON extraction instead of response_format)
  • Check that the endpoint URL points to a model that accepts the OpenAI response_format field""",

    "unknown": """\
Failure cause unclear from log output.
  • Check the log excerpt above for raw error text
  • Run with a single iteration to get cleaner output: --max-n 1
  • Check logs/llm_requests.log for what was actually sent to the model""",
}


def classify(result: dict) -> str:
    if result.get("error") == "timeout":
        return "timeout"
    if result.get("ok"):
        return "ok"
    log = (result.get("log") or "").lower()
    for name, pattern in _PATTERNS:
        if name == "timeout":
            continue
        if pattern and re.search(pattern, log):
            return name
    return "unknown"


def analyze(results: list) -> dict:
    """Group results by failure type, collect one log example per type."""
    classified = [(r, classify(r)) for r in results]
    counts = Counter(cls for _, cls in classified)
    examples: dict = {}
    for r, cls in classified:
        if cls != "ok" and cls not in examples:
            log = r.get("log", "").strip()
            # Keep the most informative lines (error lines near the end)
            lines = [ln for ln in log.splitlines() if ln.strip()]
            examples[cls] = "\n".join(lines[-6:]) if lines else "(no log captured)"
    return {
        "total": len(results),
        "ok_count": counts.get("ok", 0),
        "failure_counts": {k: v for k, v in counts.items() if k != "ok"},
        "examples": examples,
    }


def print_analysis(results: list):
    """Print failure analysis inline after a run. No-ops if no failures."""
    analysis = analyze(results)
    n_fail = analysis["total"] - analysis["ok_count"]
    if n_fail == 0:
        return

    print(f"\n── Failure analysis  {n_fail}/{analysis['total']} runs failed ──")

    for cls, count in sorted(analysis["failure_counts"].items(), key=lambda x: -x[1]):
        pct = round(count / analysis["total"] * 100)
        print(f"\n  [{cls}]  {count} run{'s' if count > 1 else ''}  ({pct}%)")

        example = analysis["examples"].get(cls, "")
        if example:
            print("  log excerpt:")
            for line in example.splitlines():
                print(f"    {line}")

        hint = _FIX_HINTS.get(cls, "")
        if hint:
            print("\n  fix hints:")
            for line in hint.splitlines():
                print(f"    {line}")

    print()
