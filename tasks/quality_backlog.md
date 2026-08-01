# Quality Backlog — from RUNS to GOOD

Verified: 2026-07-31

Rewritten 2026-07-31. The previous file described the kit/gate era — `module.py`'s check list,
`fix_classes.py`, a dozen prompt files, `runtime/kit_api*.md`, the scaffold templates,
`docs/codegen_rebuild_plan.md` Phase 7. All of it is deleted. What survived the rewrite is the
guardrails, which were never about that machinery.

## Why
A build ends with a game that RUNS. Whether it is worth playing is a human judgement and stays one
(`CLAUDE.md`: a gate may only detect broken, never "bad"). So this file is not a list of things to
gate — it is the list of what makes builds better, in the order the evidence supports: a standing
battery to notice regressions, then the prompt surface, then a snippet, then a primitive.

## Background (VERIFIED 2026-07-31)
- **The whole climbable surface is one file:** `src/maestro/codegen/prompts/build.txt`. There is no
  scaffold, no kit doc, no per-call prompt set. Every line in it is read on every turn of every
  build, so a line that does not earn its place costs the whole run.
- **The only gate is `index.html` exists.** Nothing machine-side judges a finished game.
- **Play a build:** `runtime/games/<run_id>/index.html` (3D needs http, not `file://`), or `/play`
  in the app. **Fix a build:** `cd src && python -m maestro.codegen.run --fix <run_id> "<note>"`.
- **What has been measured** lives in `docs/experiments.md`. A change to the loop that cannot point
  at a row there has not earned its place.
- **Known recurring defects** (2026-07-27 25-game grid), none yet earning more than a prompt line:
  3D scenes lit near-black (2/4, both models), fixed canvas with no window scaling (every 2D game),
  silent games (all four arcade + the deck-builder), arrow-keys-only input.

## Guardrails
- **A gate may only detect BROKEN, never "bad."** No LLM-judge in the loop, no "the game must do X"
  check (`CLAUDE.md`).
- **Blame the context before the model** — a bad build is a prompting/plumbing bug until the exact
  prompt has been dumped and read.
- **Re-run before rejecting a prompt change** — sampling variance is real. One bad output is noise.
- **A prompt fix states a general law; examples only illustrate.** Never encode the game that
  triggered it, and validate on the battery rather than the motivating case.
- **Never ship a bad example, even labelled as a failure** — examples get copied regardless of
  framing.
- **Genre battery before keeping any change:** platformer / top-down arcade / grid-turn / 3D world
  RPG. A win on one that regresses another is not a win.

## Tasks

### Q1 — A standing battery (unblocks everything else)
Nothing is run on a cadence today, so a regression between experiments is invisible.
- [ ] A repeatable battery run: fixed request list (the four genre slots above), one build each,
      results recorded with the model, the harness commit, and the cost/time per build.
      → done when: one dated battery result table exists in `docs/experiments.md` produced by a
        command that can be re-run unchanged
- [ ] A grading bar written down — what "playable", "shallow" and "good" mean concretely enough
      that a cold reader reaches the same verdict on the same build.
      → done when: the bar is written in this file and two gradings of one build agree

### Q2 — The four known defects (prompt line → snippet → primitive)
Each is a prompt line first. A primitive only after a prompt line has failed twice, across two
models (`CLAUDE.md`, "Adding a capability").
- [ ] 3D scenes lit near-black — say what is true ("light the scene so the player can see"), not
      what is forbidden. Validate on the 3D slot plus one 2D slot to confirm no cost elsewhere.
      → done when: a battery row shows the 3D build lit, with the diff to `build.txt` named
- [ ] Fixed canvas, no window scaling — every 2D game on the grid.
      → done when: a battery row shows a 2D build resizing with the window
- [ ] Silent games — no sound at all in any arcade build.
      → done when: a battery row shows a build that makes sound
- [ ] Arrow-keys-only input — bind WASD and the arrows.
      → done when: a battery row shows both bindings live

### Q3 — Settle `generate_media`
It entered unmeasured (2026-07-28). An unused schema costs every turn of every build, so a tool
that fails this comes back out.
- [ ] Measure, on the battery: does the model CALL it; does it use the returned path VERBATIM; does
      it still draw a fallback shape while the file is missing.
      → done when: a dated row in `docs/experiments.md` answers all three, with a keep/cut verdict

## Parked
- **A play-critic.** A design has to answer the judge-then-fix measurement first — 208 of one
  build's 227 steps, and a worse round 2 than round 1 (`docs/experiments.md`). Only once Q1–Q3
  plateau.
- **Cheap local depth heuristics** (a scripted-input run that reports whether the game is
  non-trivial). Report-only by construction: "the game must be non-trivial" is a does-X constraint
  and can never be a gate. Depends on the battery.
- **LLM-judge as a gate** — rejected. A 30B judging its own output adds noise, not signal.
