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

Fixtures are the intermediate JSON files a stage receives from prior stages (e.g. `premise.json`, `graph.json`). They let you run one stage in isolation without re-running the whole pipeline each time.

```bash
python eval/cli.py capture renpy --brief renpy_romance
```

Re-run this whenever the pipeline structure changes significantly or you want fresh fixture data.

### Score a stage to get a baseline

```bash
python eval/cli.py score stage renpy/premise \
  --brief renpy_romance \
  --n 5 \
  --show-reasoning
```

Read the reasoning output. If the judge says "good voice distinctness" without citing anything specific from the dialogue, the rubric criteria are too vague. Sharpen them before climbing.

Check `success_rate` first — if below 80%, fix failures before scoring. Hill climbing on a stage that fails 30% of the time is wasted compute.

### Hill climb

```bash
python eval/cli.py climb renpy/node_scripts \
  --brief renpy_romance \
  --n 5 \
  --iterations 5 \
  --mutations 5
```

Each iteration: proposes 5 prompt mutations → scores each (5 runs each) → accepts the best if it beats current p25. The winning prompt is written back to the `.txt` file at the end.

**Tuning knobs:**
- `--n` — runs per evaluation slot (baseline + each mutation). More = more reliable p25. Start with 1 to estimate how long a full climb takes, then scale up.
- `--mutations` — more mutations per iteration covers more of the search space but costs more runs.
- `--iterations` — more iterations means more refinement, but returns diminish. 5 is usually enough.

If every iteration rejects ("no improvement"), the mutation proposals are off. Usually means rubric criteria are vague — the judge doesn't know what to optimize for, so mutations are random.

**Stages with multiple prompt files** (e.g. `renpy/node_scripts` uses `scene_sketch.txt`, `character_line.txt`, `narration.txt`) climb the registered file by default. Target any other file with `--prompt-file`:

```bash
python eval/cli.py climb renpy/node_scripts --brief renpy_romance --prompt-file scene_sketch.txt
```

### Hill climb end-to-end

Per-stage climbing can't catch cross-stage regressions — a better dialogue prompt might produce output a later stage handles worse. E2e mode runs the **full pipeline** per trial and scores the combined outputs against `rubrics/<pipeline>_e2e.json`:

```bash
# climb one prompt, score the whole game it produces
python eval/cli.py climb renpy/node_scripts --e2e --prompt-file scene_sketch.txt

# single brief instead of the auto-discovered pool
python eval/cli.py climb renpy/premise --e2e --brief renpy_romance
```

Without `--brief`/`--brief-pool`, all non-character briefs are pooled and one is picked at random per run — this prevents overfitting the prompt to one genre. Each successful run also copies the built game to `eval/games/` for manual play-testing. E2e runs are slow (full pipeline × n × mutations × iterations); start with `--n 1 --iterations 1` to estimate duration.

**E2e grades the artifact, not the pipeline.** The judge receives only the built game — for renpy, the actual `game_output/game/script.rpy` read off disk — and nothing from the production bible (no premise, central_question, voice sheets, or story plan). It scores the game the way a player would: from dialogue, narration, menus, and `scene`/`show` staging alone, tracing branches by following menus and jumps. What counts as "the artifact" is a **per-pipeline** decision: each `PipelineDefinition` may set an `e2e_view(working_dir) -> str` hook (mirroring `enrich_brief`) that returns its gradable artifact; renpy's lives in `pipelines/renpy/e2e_view.py`. When a pipeline defines no view, e2e falls back to scoring the raw JSON outputs.

**The e2e rubric is axis-structured.** `rubrics/renpy_e2e.json` groups its criteria into four equal-weight axes — Narrative, Characters, Writing, Structure — and the overall score is the **mean of the four axis means**, so an axis counts 25% no matter how many criteria it holds. The score summary prints a `by axis` rollup above the per-criterion lines. Mutation still targets the single weakest criterion by p25.

**Technical validity is a gate, not a scored axis.** All four axes measure creative quality. A run whose built game fails lint or the Ren'Py build is marked failed (it lowers `success_rate` and is excluded from scoring) rather than being scored as a bad game — so a pretty-but-broken game can never post a high creative score. See `_build_gate` in `runner.py`. When no Ren'Py SDK is installed, lint cannot run and the gate passes (`error_count` is `None`).

### Score an already-built game with a chosen judge

Generation runs on the small local model, but a small model cannot critique prose — it scores broken output as good. Grade the *artifact* with a more capable (free) judge instead. `score game` points any judge connector at an already-built game directory (e.g. one saved under `eval/games/`), reads its `script.rpy`, and scores it against `rubrics/<pipeline>_e2e.json` — no pipeline re-run:

```bash
# grade a saved game with the cline connector, print per-criterion reasoning
python eval/cli.py score game renpy eval/games/renpy_romance_58b4d987 \
  --connector cline --show-reasoning
```

Point the judge at a bigger model by setting that connector's `model` in `settings.json` (e.g. the `cline` block). Without `--connector` it uses the configured default (the local model — same self-grading bias that inflates scores). Results save under `eval/results/renpy/e2e/<game_name>/`.

### Re-score saved outputs with a different judge model

Climb and score runs now save `outputs.json` alongside `summary.json`. You can re-judge those outputs with a different model (e.g. Opus via Cline) to cross-check whether your local model's scores are meaningful:

```bash
# Set cline.model to claude-opus-4-5 in settings first
python eval/cli.py rescore \
  eval/results/renpy/node_scripts/renpy_romance/<run_dir>/ \
  renpy/node_scripts \
  --connector cline \
  --compare
```

`--compare` diffs the rescore against the original judge scores side-by-side.

### Report / diff two runs

```bash
python eval/cli.py report eval/results/renpy/premise/renpy_romance/<run>/summary.json
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
cli.py score stage renpy/premise --brief renpy_romance
        │
        ├─ load brief JSON          (eval/briefs/renpy_romance.json)
        ├─ load rubric JSON         (eval/rubrics/renpy_premise.json)
        └─ load fixtures            (eval/fixtures/renpy/renpy_romance/*.json)
                │
                ▼
        StageRunner.run_n(n)
          ┌─────────────────────────────────────────────┐
          │  write fixture files → temp working dir      │
          │  call stage fn / LLM stage with those files  │  × n times
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
cli.py climb renpy/node_scripts --brief renpy_romance --n 5 --iterations 5 --mutations 5
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
                        ├─ StageRunner.run_n(n)
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
cli.py rescore eval/results/.../run_dir/ renpy/node_scripts --connector cline --compare
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

**Limitation:** With only 1 run per candidate (`--n 1`), p25 = the single run's score — noisy. Use `--n 1` to estimate duration, then scale up to `--n 5` or more for reliable results.

### FnStage vs LLMStage hill climbing

`LLMStage` stages have a `prompt_template` field pointing directly to a `.txt` file. Hill climbing swaps that file contents temporarily using `_prompt_override` (a context manager that restores the original on exit).

`FnStage` stages (most of the renpy pipeline) don't have a prompt field in the stage config — they build prompts in Python code. Hill climbing uses the stage's `prompt_file` by default. That field is metadata: it tells the climb tool which `.txt` file the function uses. The function has to actually use `render_template(_PROMPTS_DIR / stage.prompt_file, ...)` for the swap to have any effect.

Stages that make several kinds of LLM calls load several `.txt` files; `--prompt-file <name>.txt` climbs any file in the pipeline's prompts dir. If a FnStage function hardcodes its prompt inline rather than loading from a file, it is not hill-climbable.

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
  "stage": "node_scripts",
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

**Level-based rubrics** (e.g. the WIP `renpy_e2e.levels.json`) replace the 0–100 `description` with a discrete `levels` map (`0`–`4`), each level a self-contained description of what that quality looks like. Scoring becomes classification ("pick the level that matches") instead of guessing a calibrated float, which a judge does far more reliably. Display multiplies by `scale.display_multiplier` (×25 → 0–100). Authoring rules:

- **The no-context test.** The judge reads the rubric cold — zero knowledge of how it was built. Every judge-facing field (`note`, `description`, `levels`) must read as if written for a stranger. Three bleed smells to cut: (1) contrast to a prior version ("not a calibrated number", "instead of"); (2) pipeline internals ("the build gate", "the premise", "voice sheet", "the JSON outputs"); (3) authoring commentary ("anchored to a failure mode", "this game scored here"). Author-only notes are fine in underscore-prefixed fields (`_draft`, `_authoring`) — the judge prompt renders only `note` + `description` + `levels`, never those.
- **Anchor level 0 to a concrete, real failure mode** — ideally one actually seen in output.
- **Mechanical correctness the build already guarantees lands mid-scale, not top** — don't reward what the validity gate already enforces.
- **Level 4 is an attainable target, not a myth**, and define any jargon inline.
- **Stakes scale to the story's own register** — a quiet, low-stakes story can reach the top level on its own terms; never bias toward high drama.

**Axis-structured rubrics** (e.g. `renpy_e2e.json`) add an `axis` field to each criterion and a top-level `axes` list. The overall is the mean of the per-axis means — each axis weighted equally regardless of criterion count — instead of a weighted average, and `weight` is ignored. Use this when you want several independent quality dimensions to count equally:

```json
{
  "stage": "e2e",
  "pipeline": "renpy",
  "axes": ["narrative", "characters", "writing", "structure"],
  "criteria": [
    { "name": "hook_strength", "axis": "narrative", "description": "..." },
    { "name": "dialogue_craft", "axis": "writing", "description": "..." }
  ]
}
```

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
