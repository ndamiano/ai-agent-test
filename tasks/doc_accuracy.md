# Doc Accuracy — Audit & Anti-Drift Governance

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

## T1 — Initial audit (do once) — DONE 2026-07-08, SUPERSEDED
Audited all living docs against `src/` at HEAD; drift report produced; every finding fixed inline.
SUPERSEDED: that audit covered the pre-codegen-rebuild tree (IR/renpy/godot era) and most of the
docs it fixed are since deleted. A fresh pass is due — run it with T2's method.
- [x] **`CLAUDE.md`** — accurate except one stale API listing: fixed (`Preset/PRESETS` removed from
      module.py contents, real selection API named). Auth/tenancy section VERIFIED real (not
      aspirational) — every file it names exists.
- [x] **`docs/ROADMAP.md`** — accurate; no `[x]` items, all forward task-file refs resolve. No change.
- [x] **`docs/VISION.md`** — added a reconciling line (local-first architecture vs hosted paid delivery).
- [x] **`README.md`** — fixed: dead Genres table → module-catalog framing; `web` engine → `godot`;
      `validate`/`executor`/`web/` layout → `agent_loop`/`modules/`/`godot/`; "decided by validate"
      → modules' `get_errors`.
- [x] **`docs/*`** — fixed: `ir_architecture.md` Module contract (get_errors → checks-list);
      `game_creation_walkthrough.md` (`create_guards` → check's `guard`; dead `awaiting_human`/cancel);
      `tool_granularity.md` (dead `matches` component); `hitl_architecture.md` (`dirty` sidecar, not
      schema); `hitl_vision.md` D1 marked SUPERSEDED + cross-linked (resolves the awaiting_human
      contradiction).
- [x] **Drift report produced** — 4 HIGH / 4 MEDIUM / 3 LOW, all verified against code, all fixed inline.

## T2 — Anti-drift governance (standing)
- [ ] **Reinforce the same-commit rule** as a review checklist item (`CLAUDE.md` already mandates it
      under Documentation — make it explicit at review time).
- [ ] **Periodic doc-audit cadence** — a recon pass (this file's T1 is the template) each milestone,
      cross-checking doc claims vs code. Cheap to run as an agent.
- [ ] **Keep the source-of-truth map above current** — when a new doc appears, assign it an owner
      scope so facts don't get duplicated across docs.

## Ordering
T1 is a single audit pass (can run as one recon agent). T2 is ongoing. Low coupling to other
workstreams — do whenever docs feel stale; cheapest to run right after a big feature lands.
