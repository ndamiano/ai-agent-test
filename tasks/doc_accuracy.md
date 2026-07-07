# Doc Accuracy — Audit & Anti-Drift Governance

## Why
The codebase moves fast (module rewrite, combat/Godot, 3D, HD-2D all landed recently) and docs rot
behind it. `CLAUDE.md` mandates "update docs in the same commit as the code," but drift already
shows (the old `quality_todo.md` referenced `validate.py`/`executor`/genre-preset — all dead post
module-rewrite). This workstream is a one-time audit **plus** a standing practice so docs keep
representing current state.

## Guardrails
- **Fix docs to match code, not code to match docs.**
- Don't delete rationale/decision docs (`docs/game_ir_decisions.md`, `docs/ir_architecture.md`) —
  they capture the WHY.
- Avoid restating the same fact in 3 docs — duplication is where drift starts (see the source-of-
  truth map below).

## Background (VERIFIED — the living docs)
- **Root:** `CLAUDE.md` (architecture + code standards — the primary), `README.md`, `ROADMAP.md`
  (just trimmed to forward-only), `VISION.md`.
- **`docs/`:** `ir_architecture.md`, `game_ir_decisions.md`, `game_ir.schema.json`,
  `asset_manifest.schema.json`, `hitl_architecture.md`, `hitl_vision.md`, `tool_granularity.md`,
  `game_creation_walkthrough.md`, `examples/`.
- **`tasks/`:** the work plans (this dir). **`finished.md`:** shipped ledger.
- Drift evidence: pre-rewrite vocabulary (`validate.py`, `executor.build_context`, `done_condition`
  lists, genre/preset) lingered in task docs; sweep the living docs for the same.

## Source-of-truth map (who owns what — enforce, don't duplicate)
- **`CLAUDE.md`** — current architecture + how it works + code standards.
- **`VISION.md`** — the north-star / product scope (games-first, on-demand tool).
- **`ROADMAP.md`** — forward plan only (done work lives in `tasks/finished.md`, not here).
- **`README.md`** — entry point / orientation.
- **`docs/*`** — deep rationale + schemas (IR decisions, architecture, HITL model, tool granularity).
- **`tasks/*`** — active work plans; **`finished.md`** — shipped ledger.

## T1 — Initial audit (do once)
- [ ] **`CLAUDE.md`** — cross-check the architecture + "how it works" sections against current
      `src/maestro/modules/`, `engines.py`, the presenters, and the auth/scale reality (it currently
      describes a system with no auth/tenancy — note where that's now aspirational vs real). Flag any
      stale class/file/flow claims.
- [ ] **`ROADMAP.md`** — verify no `[x]` done items remain (just trimmed) and forward items are still
      real intentions.
- [ ] **`VISION.md`** — still aligned with the actual direction (games-first, on-demand, local)?
      Reconcile with the scale/auth/launch ambitions now surfacing.
- [ ] **`README.md`** — does it still orient a newcomer correctly (run commands, layout)?
- [ ] **`docs/*`** — check `ir_architecture.md`, `hitl_architecture.md`, `tool_granularity.md`,
      `game_creation_walkthrough.md` for pre-rewrite terms + describe-current-code accuracy. Verify
      `hitl_vision.md` vs `hitl_architecture.md` (the backlog says architecture supersedes vision) —
      collapse or cross-link so they don't contradict.
- [ ] **Produce a drift report** — per doc: accurate / stale-section / contradicts-code — then fix
      inline or file follow-ups.

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
