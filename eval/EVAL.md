# Eval System

---

## Why

Pipeline output quality is hard to improve without measurement. You can read outputs and have intuitions, but you can't tell if a prompt change helped on average, only on the one example you're looking at.

The eval system gives you a number. Not a perfect number — the judge is an LLM and has its own biases — but a consistent one. A consistent number lets you compare: does the new prompt score higher than the old one across many runs?

**Hill climbing** is the main use case: run the current prompt N times, measure the p25 (floor quality), propose mutations, score them, keep the best. Repeat. The prompt gets better in a direction the rubric defines.

The other use case is **diagnosis**: run `score stage --show-reasoning` before climbing and read the judge's reasoning. If it's vague or clearly wrong, the rubric criteria need sharpening before any hill climbing is meaningful.

---

## How to Use It

### Setup — capture fixtures first

Fixtures are the intermediate JSON files a stage receives from prior stages (e.g. `scenes.json`, `characters.json`). They let you run one stage in isolation without re-running the whole pipeline each time.

```bash
python eval/cli.py capture renpy --brief renpy_romance
```

Re-run this whenever the pipeline structure changes significantly or you want fresh fixture data.

### Score a stage to get a baseline

```bash
python eval/cli.py score stage renpy/dialogue \
  --brief renpy_romance \
  --time 300 \
  --max-n 10 \
  --show-reasoning
```

Read the reasoning output. If the judge says "good voice distinctness" without citing anything specific from the dialogue, the rubric criteria are too vague. Sharpen them before climbing.

Check `success_rate` first — if below 80%, fix failures before scoring. Hill climbing on a stage that fails 30% of the time is wasted compute.

### Hill climb

```bash
python eval/cli.py climb renpy/dialogue \
  --brief renpy_romance \
  --time-per-run 120 \
  --iterations 5 \
  --mutations 3
```

Each iteration: proposes 3 prompt mutations → scores each → accepts the best if it beats current p25. The winning prompt is written back to the `.txt` file at the end.

**Tuning knobs:**
- `--time-per-run` — total scoring budget per candidate. With a 120s budget and a stage that takes ~60s per run, you get ~2 runs per candidate. More runs = more reliable p25 estimate.
- `--mutations` — more mutations per iteration covers more of the search space but costs more time.
- `--iterations` — more iterations means more refinement, but returns diminish. 5–10 is usually enough.

If every iteration rejects ("no improvement"), the mutation proposals are off. Usually means rubric criteria are vague — the judge doesn't know what to optimize for, so mutations are random.

### Re-score saved outputs with a different judge model

Climb and score runs now save `outputs.json` alongside `summary.json`. You can re-judge those outputs with a different model (e.g. Opus via Cline) to cross-check whether your local model's scores are meaningful:

```bash
# Set cline.model to claude-opus-4-5 in settings first
python eval/cli.py rescore \
  eval/results/renpy/dialogue/renpy_romance/<run_dir>/ \
  renpy/dialogue \
  --connector cline \
  --compare
```

`--compare` diffs the rescore against the original judge scores side-by-side.

### Report / diff two runs

```bash
python eval/cli.py report eval/results/renpy/story/renpy_romance/<run>/summary.json
python eval/cli.py report <path_a> --compare <path_b>
```

### Validate the judge itself

```bash
python eval/judge_test.py
```

Feeds the judge a known-good and a deliberately bad output. Expect > 15-point delta. If the delta is small, the judge can't discriminate — rubric needs work.

---

## How It Works

Understanding the internals matters because the system has real failure modes.

### Flow diagrams

**Scoring a stage:**

```
cli.py score stage renpy/dialogue --brief renpy_romance
        │
        ├─ load brief JSON          (eval/briefs/renpy_romance.json)
        ├─ load rubric JSON         (eval/rubrics/renpy_dialogue.json)
        └─ load fixtures            (eval/fixtures/renpy/renpy_romance/*.json)
                │
                ▼
        StageRunner.run_timed(budget)
          ┌─────────────────────────────────────────────┐
          │  write fixture files → temp working dir      │
          │  call stage fn / LLM stage with those files  │  × N times until budget exhausted
          │  record {ok, output, elapsed}                │
          └─────────────────────────────────────────────┘
                │
                ▼
        Judge.score(output, rubric)  ← LLM call (same model by default)
          returns {scores: {criterion: {score, reasoning}}, overall}
                │
                ▼
        summarize(run_results, scored, rubric)
          builds distributions (mean, p25, p75, …) for overall + each criterion
                │
                ├─ print_summary()
                └─ save()  →  eval/results/.../summary.json
                             eval/results/.../outputs.json
```

**Hill climbing a stage:**

```
cli.py climb renpy/dialogue --brief renpy_romance --iterations 5 --mutations 3
        │
        ▼
  [baseline]  score current prompt  (same flow as above)
  current_p25 = baseline p25
        │
        └─ for each iteration:
                │
                ├─ Judge.propose_mutation(current_prompt, summary, rubric)
                │     finds weakest criterion (lowest p25)
                │     asks LLM to rewrite prompt targeting that criterion
                │     × n_mutations times  →  [mut_1, mut_2, mut_3]
                │
                └─ for each mutation:
                        │
                        ├─ write mutation text to prompt .txt file
                        ├─ StageRunner.run_timed(mutation_budget)
                        ├─ Judge.score() each output
                        ├─ summarize()  →  candidate_p25
                        └─ restore original .txt file
                                │
                                ▼
                        best_mutation = highest candidate_p25
                                │
                        ┌───────┴────────┐
                   best_p25          best_p25
                   > current_p25     ≤ current_p25
                        │                │
                   ACCEPT               REJECT
                   update current        keep current
                   prompt + summary      prompt + summary
                        │
                        ▼ (after all iterations)
                write current_prompt back to .txt file
```

**Rescore flow (cross-check with different model):**

```
cli.py rescore eval/results/.../run_dir/ renpy/dialogue --connector cline --compare
        │
        ├─ load outputs.json from run_dir   (saved by score/climb)
        ├─ load rubric
        └─ Judge(connector=cline).score() each output
                │
                ▼
        summarize + print_summary
                │
        --compare: print_diff(original summary.json, new summary)
                   shows Δp25 / Δmean per criterion
```

### Fixtures and isolation

`capture` runs the full pipeline and saves each stage's output JSON into `eval/fixtures/<pipeline>/<brief>/`. When scoring, `StageRunner` writes those files into a temp working directory before running the stage. The stage reads from disk as normal — it doesn't know it's in eval.

**Limitation:** Fixtures go stale. If you change the schema of an upstream stage (e.g. add a field to `scenes.json`), the fixtures won't have that field and the downstream stage might behave differently than it would in production.

### The judge

`Judge.score()` sends the stage output + rubric criteria to the LLM as a scoring prompt. The LLM returns JSON with per-criterion scores (0–100) and an overall. If the model returns scores as strings instead of numbers, `_weighted_average` coerces them with `float()`.

**Critical limitation:** By default, the judge uses `get_connector()` — the same model that generated the output. A model judging its own outputs has well-documented self-preference bias. Scores will be inflated. This is why `rescore` with a different model matters — if Opus consistently scores your local model's outputs 15 points lower, your climb results were optimistic.

The overall score is either returned explicitly by the judge or computed as a weighted average of criterion scores. If the model returns a flat dict (no `"scores"` wrapper), `score()` falls back to treating the whole dict as criterion scores.

### Mutation proposals

`judge.propose_mutation()` identifies the weakest criterion (lowest p25 in `by_criterion`) and asks the LLM to rewrite the prompt to improve that specific criterion. The current prompt text is included verbatim.

**Limitation:** Mutation quality depends heavily on rubric criterion descriptions. If `voice_distinctness` is described as "characters sound different", the mutation proposals will be generic. If it's described as "each character's line could only have been spoken by that character — vocabulary, sentence length, and topics are consistent with their speech_patterns field", mutations are targeted.

### p25 as the optimization target

p25 is the 25th percentile of overall scores across all runs in a scoring window. Hill climbing accepts a mutation only if the candidate's p25 beats the current p25.

Why p25 and not mean? A high mean with a low floor means the stage produces great output sometimes and broken output unpredictably. Optimizing the floor produces more consistent pipelines. Mean is still reported — if p25 goes up but mean drops, the prompt got more consistent but less ambitious.

**Limitation:** With only 1–2 runs per candidate (typical with short time budgets), p25 = the single run's score. The ranking is noisy. Use longer `--time-per-run` budgets if you're seeing iterations that alternate between accepting and rejecting.

### Time budgets and run counts

**TODO: `--time` as a scoring interface feels wrong.** Specifying a wall-clock budget to control run count is indirect — you have to know roughly how long a stage takes to pick a meaningful number, and results across sessions aren't comparable because stage latency varies. A `--n` flag (explicit run count) would be cleaner and more predictable. Revisit this.

`runner.run_timed(budget)` runs the stage in a loop until wall-clock time exceeds `budget`. The number of runs is not fixed — it depends on how long each run takes. A stage that takes 40s per run on one day might take 90s on another (model load, LM Studio queue, etc.). This means p25 estimates from different sessions aren't directly comparable.

`--per-run-timeout` marks a run as failed if it exceeds the timeout. Useful when a stuck LLM call would eat the entire budget.

### FnStage vs LLMStage hill climbing

`LLMStage` stages have a `prompt_template` field pointing directly to a `.txt` file. Hill climbing swaps that file contents temporarily using `_prompt_override` (a context manager that restores the original on exit).

`FnStage` stages (most of the renpy pipeline) don't have a prompt field in the stage config — they build prompts in Python code. Hill climbing only works on FnStages that have `prompt_file` set. That field is metadata: it tells the climb tool which `.txt` file the function uses. The function has to actually use `render_template(_PROMPTS_DIR / stage.prompt_file, ...)` for the swap to have any effect.

If a FnStage function hardcodes its prompt inline rather than loading from a file, it is not hill-climbable.

### Acceptance criterion and iteration state

The climb loop keeps a `current_prompt` and `current_summary` in memory. Each iteration compares the best mutation's p25 against `current_p25`. Accepted mutations update both. Rejected mutations are discarded — the prompt reverts to `current_prompt`.

The final prompt is written back to the `.txt` file. If you kill the process mid-climb, the file is in whatever state the last iteration left it (could be a mutation, not the best). The `_prompt_override` context manager only protects mutation runs — not the final write.

### What the system does NOT do

- It does not catch regressions in other stages when you improve one. A better dialogue prompt might produce outputs that characters.py handles less well. End-to-end scoring catches this; per-stage scoring doesn't.
- It does not validate that judge scores correlate with human judgment. Run `judge_test.py` and occasionally read `--show-reasoning` output to spot-check.
- It does not hill-climb the rubric itself. Bad rubric criteria produce misleading climb results. You have to catch this by reading judge reasoning.

---

## Reference

### Directory layout

```
eval/
  briefs/          # Pipeline inputs — one JSON per scenario
  rubrics/         # Scoring criteria — one JSON per stage
  fixtures/        # Captured stage inputs (gitignored)
  results/         # Saved score summaries + outputs (gitignored)
  cli.py           # Entry point
  runner.py        # Runs stages/pipelines in isolation with timeboxes
  judge.py         # LLM-as-judge scoring + mutation proposals
  report.py        # Summarise, diff, and print results
  climb.py         # Hill-climbing loop
  failures.py      # Failure classification + fix hints
  fixtures.py      # Capture and load fixture files
  judge_test.py    # Discrimination test for the judge
```

### Writing a rubric

```json
{
  "stage": "dialogue",
  "pipeline": "renpy",
  "criteria": [
    {
      "name": "voice_distinctness",
      "description": "Each character's lines could only have been spoken by that character. Vocabulary, sentence length, and topic choices are consistent with their speech_patterns field. No two characters are interchangeable.",
      "weight": 1.5
    },
    {
      "name": "subtext",
      "description": "Characters do not state their emotional state or intentions directly. Meaning is conveyed through what they choose to say, avoid, or deflect. A reader should be able to infer what each character wants without being told.",
      "weight": 1.0
    }
  ]
}
```

- `name` must be a valid Python identifier.
- `description` is the judge's scoring instruction — write it as a specific, testable claim.
- Higher `weight` shifts p25 toward this criterion.
- File must be named `<pipeline>_<stage>.json`.

### Interpreting score output

```
overall:  mean=72.4  p25=61.0  p75=81.0  min=54.0  max=88.0
by criterion:
  voice_distinctness        mean=68.2  p25=58.0  p75=75.0
  subtext                   mean=74.5  p25=65.0  p75=83.0
```

- **p25 < 50** — stage fails a quarter of the time by this criterion.
- **p75 − p25 > 25** — high variance, output is unreliable.
- **success_rate < 0.80** — too many errors; fix before climbing.
- **Criterion with lowest p25** — that's what the next climb iteration will target.
