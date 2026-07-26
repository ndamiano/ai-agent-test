# Doc Accuracy — Audit & Anti-Drift Governance

Verified: 2026-07-25

## Why
The codebase moves fast (module rewrite, combat/Godot, 3D, HD-2D all landed recently) and docs rot
behind it. `CLAUDE.md` mandates "update docs in the same commit as the code," but drift already
shows (the old `quality_todo.md` referenced `validate.py`/`executor`/genre-preset — all dead post
module-rewrite). This workstream is a one-time audit **plus** a standing practice so docs keep
representing current state.

## Guardrails
- **Fix docs to match code, not code to match docs.**
- Don't delete rationale/decision docs — they capture the WHY. (The IR-era ones named here
  previously went with the IR itself in the codegen rebuild.)
- Avoid restating the same fact in 3 docs — duplication is where drift starts (see the source-of-
  truth map below).

## Background (VERIFIED — the living docs, updated 2026-07-23)
- **Root:** `CLAUDE.md` (architecture + code standards — the primary), `README.md`.
- **`docs/`:** `ROADMAP.md` (forward-only), `VISION.md`, `DEPLOY.md`, `codegen_rebuild_plan.md`,
  `compute_billing_plan.md`, `asset_pipeline_chaining.md`, `hitl_vision.md`. (The IR-era docs —
  `ir_architecture.md`, `game_ir_decisions.md`, the schemas, `hitl_architecture.md`,
  `tool_granularity.md`, `game_creation_walkthrough.md`, `examples/` — are deleted.)
- **`tasks/`:** the work plans (this dir). **`finished.md`:** shipped ledger.
- Drift evidence: pre-rewrite vocabulary (`validate.py`, `executor.build_context`, `done_condition`
  lists, genre/preset) lingered in task docs; sweep the living docs for the same.

## Source-of-truth map (who owns what — enforce, don't duplicate)
- **`CLAUDE.md`** — current architecture + how it works + code standards.
- **`docs/VISION.md`** — the north-star / product scope (games-first, on-demand tool).
- **`docs/ROADMAP.md`** — forward plan only (done work lives in `tasks/finished.md`, not here).
- **`README.md`** — entry point / orientation.
- **`docs/*`** — deep rationale + plans (codegen rebuild, compute/billing, asset chaining, HITL
  vision, deploy).
- **`tasks/*`** — active work plans; **`finished.md`** — shipped ledger.

## T1 — Post-rebuild audit (due) — KNOWN DRIFT, verified 2026-07-25
The 2026-07-08 audit is retired (it covered the pre-codegen-rebuild tree; most docs it fixed are
deleted). Concrete drift already confirmed against HEAD — the gate list and the draw surface both
changed and the docs did not follow:
- [ ] **`README.md`** — `:9` names the gate chain "typecheck → headless → **probe** → render →
      **scroll**" and `:80-81` lists `probe`/`scroll` runners; both gates were deleted (commit
      `0ef019f`, `CLAUDE.md` § THE GATES DETECT BROKEN). Also still says an "executor" drives the
      build (it's a completion-driven job chain now) and describes a `draw` hook the game no longer
      has.
      → done when: `grep -c "probe\|scroll\|executor" README.md` is 0 (currently 4 matching lines)
- [ ] **`docs/ROADMAP.md`** — `:17`, `:56`, `:67`, `:73`, `:80-81`, `:103` all plan probe work
      (`dead_movement`, `dead_action`, `unbound_control`, "harden the probe"); the probe is gone.
      `:56` also lists `draw` among the authored hooks.
      → done when: `grep -c "probe" docs/ROADMAP.md` is 0 (currently 7 matching lines)
- [ ] **Sweep the rest** with T2's method (`CLAUDE.md`, `docs/VISION.md`, `docs/DEPLOY.md`,
      `docs/codegen_rebuild_plan.md`) for the same two changes, then produce a drift report.
      → done when: `grep -c "probe\|scroll" docs/VISION.md docs/DEPLOY.md` is 0 for both (clean today)

## T2 — Anti-drift governance (standing)
- [ ] **Reinforce the same-commit rule** as a review checklist item (`CLAUDE.md` already mandates it
      under Documentation — make it explicit at review time).
      → done when: `grep -A3 "### Code review" CLAUDE.md` mentions "docs" or "documentation"
- [ ] **Periodic doc-audit cadence** — a recon pass (this file's T1 is the template) each milestone,
      cross-checking doc claims vs code. Cheap to run as an agent.
      → done when: a recon entry dated after 2026-07-25 appears in this file's T1 section
- [ ] **Keep the source-of-truth map above current** — when a new doc appears, assign it an owner
      scope so facts don't get duplicated across docs.
      → done when: `grep -c "narrative_design" tasks/doc_accuracy.md` is ≥1 (currently 0 — missing)

## Ordering
T1 is a single audit pass (can run as one recon agent). T2 is ongoing. Low coupling to other
workstreams — do whenever docs feel stale; cheapest to run right after a big feature lands.
