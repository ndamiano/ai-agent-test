# Build Loop — the codegen pipeline itself

Verified: 2026-07-25

## Why
The north-star work — the model authoring a game against the kit until the gates pass — had **no
task file**. It lived in `docs/codegen_rebuild_plan.md`, which is now history, not a plan: zero
checkboxes, branch `codegen-rebuild`, and a vocabulary the code dropped (`AgentLoop`, a single-file
`games/<slug>.js`, a `draw` hook, the probe). A cold agent reading `tasks/` therefore concluded the
project was a billing system. This file is the live plan for the loop.

## Boundary with the other quality files (do not duplicate)
- **This file** — the LOOP: gates, fix shapes, step economics, the kit surface, the audit.
- **`quality_backlog.md`** — JUDGING output quality: grading skills, the play-critic, prompt
  hill-climbing against the battery.
- **generation-quality group** (`world_first`, `game_style`, `asset_quality`, `game_media`) — what
  goes INTO a game: content, art, audio.

## Background (VERIFIED — points at code)
- Driver: `src/maestro/codegen/build_chain.py` (default `max_steps=200`), cursor in
  `build_state.py`, per-shape machines in `build_steps.py`, gate list in `module.py`.
- Attribution: `eval/build_trace.py` — per-build steps grouped by failing error class, wasted steps
  (a class recurring after it was cleared), elapsed; diffs two runs of the same premise. This is the
  tool that justified deleting the probe (13% of steps, 8 dead builds).
- Spec-side surface: `runtime/kit_catalog.md` § "What the runtime CANNOT do" — the only thing
  stopping a spec promising what the runtime can't deliver (today: sound, screen effects, typing,
  persistence/multiplayer).
- Audit: `src/maestro/codegen/audit.py`, verdicts in `runs/<id>/audit_verdicts.jsonl`.

## Guardrails
- **A gate may only detect BROKEN, never "bad"** (`CLAUDE.md`). Proposing a "the game must DO X"
  check is the one reliably wrong move here — that is what the probe and scroll gates were.
- **Widen the kit, don't prompt harder** for hard/ambiguous mechanics. Widening the kit means
  widening `kit_api*.md` AND `kit_catalog.md`, or specs never reach the new primitive.
- **Measure before changing the loop.** `eval/build_trace.py` over real builds, not intuition — the
  probe's benefit was asserted for months and was never once verified.
- Normalize model output to its evident intent; never add caps or rules that DROP it.

## B1 — Retire the dead plan
- [ ] Mark `docs/codegen_rebuild_plan.md` SUPERSEDED at the top (it is the WHY-record of the
      rebuild, so keep it — `doc_accuracy.md` guardrail), and repoint `CLAUDE.md` +
      `docs/ROADMAP.md` at this file for live loop work.
      → done when: `grep -rn "codegen_rebuild_plan" CLAUDE.md docs/ROADMAP.md` returns only
      historical references.

## B2 — Step-economics baseline
No current measurement of where a build's 200 steps go. Every loop change below should be judged
against it, the way the probe deletion was.
- [ ] Run `python -m eval.build_trace` over the last N builds; record the class → % of steps table
      in this file, plus wasted-step rate (regressions) and median steps-to-green.
      → done when: this section holds a dated table covering ≥10 builds.
- [ ] Re-run after any loop change and diff, per the guardrail.
      → done when: standing.

## B3 — Kit widening queue
Breadth comes from the model composing primitives. The CANNOT list is the demand signal: each entry
is either a gap to close or a permanent constraint, and today nothing records which.
- [ ] Give each `kit_catalog.md` CANNOT entry a verdict — widen (with the primitive family it
      implies) or permanent-by-design. Audio is owned by `game_media.md` M1; the rest are unassigned.
      → done when: every CANNOT bullet carries a verdict and the widen ones are tasks somewhere.
- [ ] For each widening that lands: kit primitive + `kit_api*.md` section + worked example +
      `kit_catalog.md` entry, in the same commit.
      → done when: standing rule; a widening missing its catalog entry is the failure.

## B4 — The audit's known ceiling
The spec-vs-code audit certifies the spec AS WRITTEN: a shallow spec certifies shallow (the measured
case — an unwinnable fight, because no claim promised winnable combat). Spec richness is the lever,
and it sits upstream in `spec_draft`.
- [ ] Measure it first: claims-per-spec (the audit enumerates them mechanically) across the recent
      spec corpus, and how many are load-bearing vs restatements.
      → done when: a dated median + distribution is recorded here.
- [ ] Only then decide whether `spec_draft` should be pushed for depth — a richer spec is more
      claims to satisfy, so this can cost build steps rather than buy quality. Judge on B2's table.
      → done when: a decision with its evidence is written here.

## B5 — Snapshot the game folder at built states
Audit rounds re-edit code that already passed, so regressions are structurally likely, and nothing
preserves the last-green artifact — `snapshot` in `build_chain.py` is the stall-detection cursor,
not the game.
- [ ] Snapshot `game/` at each green finalize (and before an audit-driven fix round), retrievable
      per run.
      → done when: a run dir holds ≥2 recoverable built states and a test asserts one restores.

## Parked
- Play-critic / vision-in-loop — `quality_backlog.md` Q4; the honest end of the verification gap,
  deliberately last.
- Reasoning-only turns (~3-7%, clustered on big authoring prompts) — ruled: a failed turn is a
  failed turn, retry. No salvage-from-reasoning.
