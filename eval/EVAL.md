# Eval System

Grades a finished game with an LLM judge against a rubric. Generation runs on a small local model that can't critique its own prose, so quality is measured by pointing a more capable judge at the built artifact.

> **Scope.** Hill-climbing (judge-scored prompt mutation) was removed in the rebuild and is slated to return; today eval only grades finished artifacts. Prompts are kept as swappable `.txt` files so climbing can come back.

## score game

Point a judge at an already-built game directory; it reads the artifact and scores it against `rubrics/<pipeline>_e2e.json` — no rebuild.

```bash
python eval/cli.py score game renpy <run_dir>/game_output --connector cline --show-reasoning
```

- `--connector` picks the judge (`cline` | `openrouter` | `lmstudio`). Without it, the configured default — the local model — judges its own output; self-preference inflates scores, so grade with a bigger model. Set that connector's `model` in `settings.json`.
- `--show-reasoning` prints per-criterion reasoning. Vague reasoning means vague criteria.
- `--rubric` overrides the default rubric path; `--no-save` skips writing results.
- The judge sees only the built game (for Ren'Py, `game_output/game/script.rpy` off disk). It scores the way a player would — dialogue, narration, menus, staging — tracing branches by following menus and jumps.
- Results save under `eval/results/<pipeline>/e2e/<game_name>/`.

## report

```bash
python eval/cli.py report <summary.json>
python eval/cli.py report <path_a> --compare <path_b>   # diff two runs
```

## The judge

`Judge.score()` sends the artifact + rubric to the LLM and returns per-criterion scores + an overall. Check it discriminates:

```bash
python eval/judge_test.py   # feeds a known-good + a deliberately-bad output; expect a wide delta
```

A model judging its own output scores it high — always grade with a different, more capable model than the one that generated.

## Writing a rubric

The live rubric is `rubrics/renpy_e2e.json` — **level-based** and **axis-structured**.

**Level-based.** Each criterion is scored by a discrete `levels` map (`0`–`4`), each level a self-contained description of what that quality looks like. Scoring becomes classification ("pick the level that matches") instead of guessing a calibrated float, which a judge does far more reliably. Display multiplies by `scale.display_multiplier` (×25 → 0–100).

- **The no-context test.** The judge reads the rubric cold. Every judge-facing field (`note`, `description`, `levels`) must read as if written for a stranger. Three bleed smells to cut: contrast to a prior version ("not a calibrated number"); build internals ("the premise", "voice sheet"); authoring commentary ("anchored to a failure mode"). Author-only notes go in underscore-prefixed fields (`_draft`, `_authoring`) — the judge prompt renders only `note` + `description` + `levels`.
- **Anchor level 0 to a concrete, real failure mode** — ideally one actually seen in output.
- **Mechanical correctness the build already guarantees lands mid-scale, not top.**
- **Level 4 is an attainable target, not a myth**; define any jargon inline.
- **Stakes scale to the story's own register** — a quiet story can reach the top level on its own terms; never bias toward high drama.

**Axis-structured.** Each criterion carries an `axis`; a top-level `axes` list groups them. The overall is the mean of the per-axis means — each axis weighted equally regardless of criterion count (`weight` is ignored). `renpy_e2e.json` uses four equal axes: Narrative, Characters, Writing, Structure.

```json
{
  "stage": "e2e",
  "pipeline": "renpy",
  "axes": ["narrative", "characters", "writing", "structure"],
  "criteria": [
    { "name": "hook_strength", "axis": "narrative", "levels": { "0": "...", "4": "..." } },
    { "name": "dialogue_craft", "axis": "writing", "levels": { "0": "...", "4": "..." } }
  ]
}
```

**Technical validity is a gate, not a scored axis.** A game that fails lint / the Ren'Py build is marked failed and excluded from scoring rather than scored as a bad game, so a pretty-but-broken game can never post a high creative score. With no Ren'Py SDK installed, lint can't run and the gate passes.

**Criterion gates (a load-bearing criterion caps the overall).** A criterion may declare `"gate": {"threshold": N}`. When the judge scores it below level `N`, the overall is capped at that criterion's own score — the rest of the rubric can't lift it. Use this for a criterion that is the whole game: if it fails, nothing else matters. The live example is `coherence` in `renpy_node_scripts.json` (`threshold: 2`) — a scene of fluent nonsense scores *as* nonsense no matter how distinct its voices or tight its economy. Applied in `_apply_gates` after the overall is computed; inert on rubrics that declare no gate. Keep the gate description in an `_authoring` field, not judge-facing text — the judge scores the level normally and never sees the cap mechanic.

## Files

```
eval/
  cli.py            # entry point — score game, report
  judge.py          # LLM-as-judge scoring
  judge_test.py     # discrimination test for the judge
  report.py         # summarise + diff results
  rubrics/          # scoring criteria (renpy_e2e.json is the live one)
  games/            # built games saved for grading
  results/          # saved score summaries (gitignored)
```
