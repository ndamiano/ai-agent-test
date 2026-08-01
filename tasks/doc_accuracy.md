# Doc Accuracy — Audit & Anti-Drift Governance

Verified: 2026-07-31

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

## Background (VERIFIED 2026-07-31 — the living docs)
- **Root:** `CLAUDE.md` (architecture + code standards — the primary), `README.md`.
- **`docs/`:** six files, all live — `vision.md`, `roadmap.md`, `architecture.md`, `deploy.md`,
  `compute_billing_plan.md`, `experiments.md`.
- **`tasks/`:** the work plans (this dir). **`finished.md`:** shipped ledger.
- What a sweep looks for: vocabulary from a retired surface. Today that is the kit era —
  `module.py`, `fix_classes.py`, `kit_api*.md`, `kit_catalog.md`, the scaffold templates, the probe
  and scroll gates, the `draw` hook, `executor`.

## Source-of-truth map (who owns what — enforce, don't duplicate)
- **`CLAUDE.md`** — current architecture + how it works + code standards.
- **`docs/vision.md`** — the north-star / product scope (games-first, on-demand tool).
- **`docs/roadmap.md`** — forward plan only (done work lives in `tasks/finished.md`, not here).
- **`README.md`** — entry point / orientation.
- **`docs/architecture.md`** — the system as processes: the two planes, where state lives, trust
  boundaries.
- **`docs/deploy.md`** — the prod runbook.
- **`docs/compute_billing_plan.md`** — credits, metering, the cost model.
- **`docs/experiments.md`** — what has been RUN against the loop and what it measured.
- **`tasks/*`** — active work plans; **`finished.md`** — shipped ledger.

## T1 — Post-rebuild audit — DONE, re-verified 2026-07-31
- [x] **`README.md`** — describes the six-tool driver, the one gate, and the fix path.
      `grep -c "probe\|scroll\|executor" README.md` is 0.
- [x] **`docs/roadmap.md`** — no probe work planned. `grep -c "probe" docs/roadmap.md` is 0.
- [x] **Sweep the rest** — `CLAUDE.md`, `docs/vision.md`, `docs/deploy.md` carry none of the dead
      vocabulary either. `docs/experiments.md` names the probe once, as the record of what it
      measured, which is that file's job.

**Recon 2026-07-31:** swept `README.md`, `CLAUDE.md`, `docs/*.md` for the retired surface (probe,
scroll, executor, kit_api/kit_catalog, `module.py`, `fix_classes.py`, the `draw` hook, scaffold
templates). Living docs clean. Drift found and fixed the same day, outside this file:
`CLAUDE.md`'s north star claimed local-only inference, `tasks/quality_backlog.md` was written
against the deleted kit, `tasks/launch_plan.md` assumed a one-genre launch, and
`tasks/test_health.md`/`tests/README.md` described a suite twice the current size.

## T2 — Anti-drift governance (standing)
- [ ] **Reinforce the same-commit rule** as a review checklist item (`CLAUDE.md` already mandates it
      under Documentation — make it explicit at review time).
      → done when: `grep -A3 "### Code review" CLAUDE.md` mentions "docs" or "documentation"
- [x] **Periodic doc-audit cadence** — a recon pass (this file's T1 is the template) each milestone,
      cross-checking doc claims vs code. Cheap to run as an agent. Latest: 2026-07-31, in T1.
      → done when: standing; each milestone adds a dated recon entry to T1
- [ ] **Keep the source-of-truth map above current** — when a new doc appears, assign it an owner
      scope so facts don't get duplicated across docs.
      → done when: standing; every file in `docs/` appears in the map above with one owner

## Ordering
T1 is a single audit pass (can run as one recon agent). T2 is ongoing. Low coupling to other
workstreams — do whenever docs feel stale; cheapest to run right after a big feature lands.
