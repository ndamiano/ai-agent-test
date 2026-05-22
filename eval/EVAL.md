# Eval System

Scores pipeline stage output quality and hill-climbs prompt templates to improve it.

---

## Concepts

**Brief** — the input to a pipeline run (genre, tone, premise, etc). Lives in `eval/briefs/<name>.json`.

**Fixture** — the intermediate JSON files a stage would normally receive from prior stages. Captured once from a real pipeline run so stages can be scored in isolation without re-running the whole pipeline.

**Rubric** — criteria + weights for judging a stage's output. Lives in `eval/rubrics/<pipeline>_<stage>.json`. Scores are 0–100.

**Hill climbing** — iterative prompt improvement: score → find weakest criterion → propose N mutations → score each → keep the best if it improves p25 → repeat.

**p25** — the optimization target. 25th percentile of overall scores across runs. Optimizing the floor (worst-case quality) rather than the mean is more useful — a high mean with a low floor means the pipeline fails unpredictably.

---

## Quick Start

```bash
# 1. Run the pipeline once to capture intermediate outputs as fixtures
python eval/cli.py capture renpy --brief renpy_romance

# 2. Score a single stage (runs the stage N times within a time budget)
python eval/cli.py score stage renpy/story --brief renpy_romance --time 120 --max-n 5

# 3. Score with output + judge reasoning visible (for spot-checking)
python eval/cli.py score stage renpy/story --brief renpy_romance --time 120 --max-n 3 --show-reasoning

# 4. Hill-climb a stage's prompt
python eval/cli.py climb renpy/scene --brief renpy_romance --time-per-run 120 --iterations 5
```

---

## Directory Layout

```
eval/
  briefs/          # Pipeline inputs — one JSON per scenario
  rubrics/         # Scoring criteria — one JSON per stage
  fixtures/        # Captured stage inputs (gitignored)
  results/         # Saved score summaries (gitignored)
  cli.py           # Entry point — all commands live here
  runner.py        # Runs stages/pipelines in isolation with timeboxes
  judge.py         # LLM-as-judge scoring + mutation proposals
  report.py        # Summarise, diff, and print results
  climb.py         # Hill-climbing loop
  failures.py      # Failure classification + fix hints
  fixtures.py      # Capture and load fixture files
  judge_test.py    # One-shot discrimination test for the judge
```

---

## CLI Reference

### `capture`
Run the pipeline once end-to-end and save all intermediate JSON outputs as fixtures.
```bash
python eval/cli.py capture renpy --brief renpy_romance
```
Re-run whenever the pipeline structure changes or you want fresh fixture data.

### `score stage`
Score a single stage within a wall-clock time budget. Runs the stage repeatedly, judges each output, reports aggregate statistics.
```bash
python eval/cli.py score stage renpy/story \
  --brief renpy_romance \
  --time 300 \              # total wall-clock budget (seconds)
  --max-n 10 \              # hard cap on run count
  --per-run-timeout 60 \    # fail a run if it exceeds this
  --show-reasoning          # print output + judge reasoning per run
```

### `score pipeline`
Score a full pipeline end-to-end. Requires an e2e rubric at `eval/rubrics/<pipeline>_e2e.json`.
```bash
python eval/cli.py score pipeline renpy --brief renpy_romance --time 1800
```

### `climb`
Hill-climb a stage's prompt template. Only works on `LLMStage` stages (those with a `.txt` prompt file in the pipeline's `prompts/` directory). FnStages with inline prompts are not currently hill-climbable.
```bash
python eval/cli.py climb renpy/scene \
  --brief renpy_romance \
  --time-per-run 120 \      # scoring budget per candidate prompt
  --iterations 10 \         # number of improvement rounds
  --mutations 3             # candidate prompts to test per round
```
The winning prompt is written back to the template file at the end. It starts from whatever text is in the file, so you can seed it with any prompt you like.

### `report`
Print a saved summary or diff two summaries.
```bash
python eval/cli.py report eval/results/renpy/story/renpy_romance/<run>/summary.json
python eval/cli.py report <path_a> --compare <path_b>
```

---

## Interpreting Results

```
overall:  mean=72.4  p25=61.0  p75=81.0  min=54.0  max=88.0
by criterion:
  specificity               mean=68.2  p25=58.0  p75=75.0
  structure                 mean=74.5  p25=65.0  p75=83.0
  downstream_utility        mean=76.1  p25=68.0  p75=82.0
  originality               mean=71.0  p25=55.0  p75=79.0
```

- **p25** — floor quality. If p25 < 50, the stage fails a quarter of the time.
- **p75 − p25** — spread. Narrow spread (< 10) = consistent. Wide spread (> 25) = unreliable.
- **Criterion gaps** — weak criterion with low p25 is the hill-climb target.
- **success_rate** — runs that completed without error. Below 80% means fix failures before hill climbing.

---

## Writing a Rubric

```json
{
  "stage": "story",
  "pipeline": "renpy",
  "criteria": [
    {
      "name": "specificity",
      "description": "Story has concrete, named details — specific character motivations, named stakes, defined arc shape. Not vague genre conventions or placeholder text.",
      "weight": 1.0
    },
    {
      "name": "downstream_utility",
      "description": "The output would give a character/scene generator enough concrete material to work with. All required fields are populated with usable, non-placeholder content.",
      "weight": 1.5
    }
  ]
}
```

Rules:
- `name` must be a valid Python identifier (used as dict key).
- `description` is fed verbatim to the judge LLM — write it as a specific, testable claim, not a vague label.
- Higher `weight` means this criterion drives p25 more. Use 1.5 for criteria that block downstream stages if wrong.
- File must be named `<pipeline>_<stage>.json` (e.g. `renpy_story.json`).

---

## Hill Climbing Workflow

1. **Capture fixtures** — do this first so scoring doesn't re-run prior stages.
2. **Score the baseline** with `--show-reasoning` and `--max-n 5`. Read the reasoning — if it's generic ("good specificity"), the judge is not discriminating and rubric criteria need sharpening.
3. **Check success rate** — if below 80%, fix failures first (see failure analysis output). Hill climbing on a broken stage wastes iterations.
4. **Run climb** — start with 3 mutations and 5 iterations. Watch the `Δp25` column. If every iteration rejects, the mutation proposals are off — check the rubric criteria descriptions.
5. **Validate the winner** — after climb, read the final prompt. Run `score stage --show-reasoning --max-n 3` to check reasoning quality improved, not just scores.
6. **Commit the prompt** — the climb writes the winning prompt back to the `.txt` file. Commit it as a deliberate improvement with the score delta in the message.

---

## Adding a New Brief

Create `eval/briefs/<name>.json` with the same fields the pipeline expects in its `brief.json`. For the renpy pipeline:

```json
{
  "genre": "horror",
  "tone": "dread and dark humor",
  "premise": "A groundskeeper discovers the estate's garden grows faster at night.",
  "notes": "Lean into mundane detail as contrast to supernatural elements.",
  "character_count": "3",
  "scene_count": "3",
  "setting": "English countryside, late Victorian"
}
```

Then `capture` and `score` as normal.

---

## Judge Validation

Run `python eval/judge_test.py` to check the judge can discriminate good output from deliberately bad output. Expect > 15-point delta on a 0–100 scale. If delta < 15, the judge prompt or rubric criteria need work before hill climbing results are meaningful.
