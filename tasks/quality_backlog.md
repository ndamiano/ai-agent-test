# Quality Backlog — from RUNS to GOOD

Verified: 2026-07-25

## Why
The north star is *good* output, not *valid* output. The local gates
(`src/maestro/codegen/module.py`: planned → data → authored → contracted → typechecks →
single_mover → runs → plays → renders → scrolls) prove a game RUNS and its controls DO
something — nothing judges whether it's FUN or well-written. Judge-in-loop is THE lever, and
the plan (do not re-litigate) is: climb the prompt surface + cheap local heuristics NOW, and
the vision play-critic LAST (`docs/codegen_rebuild_plan.md` Phase 7 — frontier, cloud,
explicitly out of local scope). Blame context before model: a bad build is a prompting/plumbing
bug until the exact prompt has been dumped and read.

## Background (VERIFIED 2026-07-23)
- Gate list: `CodegenModule.checks` in `src/maestro/codegen/module.py`; each `Error.code`
  routes to a fix shape in `src/maestro/codegen/build_chain.py` via
  `src/maestro/codegen/fix_classes.py`.
- The climbable prompt surface: `src/maestro/codegen/prompts/*.txt` (spec_draft, plan_game,
  design_data, author_file, fix_loop, fix_data, plan_assets, plan_meshes, reskin_draw,
  reskin_mesh, contract_rules, contract_invariant, `fix_kinds/*.txt`), the injected kit docs
  `runtime/kit_api.md` / `runtime/kit_api_3d.md` / `runtime/kit_catalog.md`, and the scaffold
  sources `src/maestro/codegen/scaffold_templates/*.ts.tmpl` (real TS we own, climbable like
  prompts).
- Iteration workflow: NONE. Q1 writes it — a babysat launch → monitor → grade → root-cause → fix →
  rebuild loop, and a grading bar built on the why-chain/nonsense test.
- Current CLI: `cd src && python -m maestro.codegen.run "<request>"`; fix path
  `--fix <run_id> "<note>"`; play via `runtime/index.html?game=<slug>`.
- Phase 7 (`docs/codegen_rebuild_plan.md`): T7.1 self-play metrics (solvable/non-trivial/fair,
  computable local-ish), T7.2 play-critic (vision+control agent judging fun/feel).

## Guardrails
- **REJECT making local gates judge "good".** Gates stay deterministic invariants; quality
  judgment is skills-workflow now, play-critic later. No LLM-judge `Check` on the local model.
- **REJECT chasing the scroll-gate false positive** — known-open by choice (single-screen
  arcade games can't satisfy it; the model inflates the world chasing it). Don't "fix" games
  to green it.
- **Re-run before rejecting a prompt change** — seed/sampling variance is real (spec-draft
  variance alone can flip `world`/scheme). One bad output is noise.
- **Never ship a bad example, even labelled as a failure** — examples get copied regardless of
  framing. Proven law; it survives the stack rewrite.
- Genre battery before keeping any prompt change: platformer / top-down arcade / grid-turn /
  3D world RPG. A win on one that regresses another is not a win.

## Tasks

### Q1 — Write the iteration skills against codegen (unblocks everything else)
- [ ] An iterate skill: launch via `python -m maestro.codegen.run`, monitor
      `runs/<id>/build_state.json`, root-cause → fix → rebuild. Two rules it must hold:
      /proc-not-pgrep liveness, and blame the context before the model.
      Verify: run one build end-to-end by the skill's own text.
      → done when: `python -m maestro.codegen.run "<req>"` finishes and `runs/<id>/build_state.json` has `"phase": "done"`
- [ ] A grade skill: the calibrated bar + why-chain/nonsense test, pointed at generated games'
      dialogue and playable feel via `runtime/index.html?game=<slug>`. Needs a gold example to
      grade against — pick one from a real build and check it in.
      → done when: the skill file exists and names a gold example that is checked in
- [ ] Pick ONE gold spec per genre-battery slot and record it in the skill (the old gold A/B
      premise pattern). Verify: two graders (you, cold) reach the same verdict on one build.
      → done when: the skill file has a dated table naming one spec each for platformer/top-down/grid-turn/3D-RPG

### Q2 — Prompt hill-climb (the standing grind)
- [ ] Build the tracking table here: every file in the Background prompt-surface list →
      what call it backs → last climbed → verdict. Include kit_api*.md and scaffold templates.
      → done when: this file has a table row for every file in src/maestro/codegen/prompts/*.txt, scaffold_templates/*.tmpl and runtime/kit_api*.md/kit_catalog.md
- [ ] One climb pass per prompt: baseline build → grade (Q1 skills) → hypothesis → change →
      rebuild → keep only if better across the battery. Files: the prompt under test only.
      → done when: the Q2 tracking table has a non-empty "last climbed"/"verdict" for ≥1 file
- [ ] `spec_draft.txt` first — a wrong spec is a wrong everything, and it's human-gated so a
      better draft is pure win. Watch for the known variance flipping `world`/scheme.
      → done when: the tracking table's `spec_draft.txt` row has a non-empty verdict

### Q3 — Cheap local "good" heuristics (Phase 7 T7.1, early slice)
- [ ] Self-play depth heuristic: a NEW runner (`runtime/depth.mjs` — the old `probe.mjs` was
      deleted with the probe gate) doing a scripted-input run that checks the game is non-trivial
      (e.g. random-input run should LOSE more often than no-input; win requires acting). Test:
      fixture game in `runtime/games/` that trivially wins fails the heuristic.
      → done when: `runtime/depth.mjs` exists and a trivially-winning fixture in `runtime/games/` fails it
- [ ] Report, NEVER gate. "The game must be non-trivial" is a DOES-X constraint, so it can only
      ever be a report — making it blocking is the exact shape `CLAUDE.md` forbids (it is why the
      probe is gone). Collect verdicts across builds; a blocking version is not a later phase.
      → done when: verdicts appear in `events_for` (db/store.py) for ≥3 builds and no `Check` in `module.py` wires it

### Q4 — Play-critic (deferred, cloud — comes LAST)
- [ ] Design only when Q1–Q3 plateau: vision+control agent plays the staged `/play` build,
      judges fun/feel against a rubric, emits fix notes into the existing
      `--fix <run_id> "<note>"` path (the human-note fix already classifies to `default`).
      Do not start before the local surface is climbed.
      → done when: `docs/codegen_rebuild_plan.md`'s T7.2 bullet is expanded into a rubric + architecture section

## Parked
- VN-era items (per-branch continuity, combat/economy depth checks, puzzle-depth, revise_node
  tooling, the eval rubric CLI) — all referenced deleted IR machinery; re-derive from scratch
  if a codegen analog is ever needed.
- Scroll-gate false positive — open by choice, see Guardrails.
- LLM-judge as a local gate — rejected for now (a 30B judging its own output adds noise, not
  signal); the play-critic slot owns judgment.
