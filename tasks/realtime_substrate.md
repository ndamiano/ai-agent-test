# Real-Time Substrate — Continuous Time Instead of Ticks

## Why
The only built substrate is `discrete_state` — turn/tick-based, one action resolves at a time. Whole
genres (action, real-time sim, live movement + combat, anything with simultaneous continuous actors)
need a **real-time** execution model. It's already *designed* (`real_time_sim`) but **not built**.
This workstream builds it.

## Background (from `CLAUDE.md` + `docs/ir_architecture.md`)
- **A game = one substrate (execution model) + composed mechanic-modules.** `discrete_state` is the
  only one built; **`real_time_sim` is designed, not built** (stated in CLAUDE.md).
- `spec["substrate"] = "discrete"` today is the only value; `spec_tools` + the proposer only ever
  pick discrete. `run.run_build`, modules, and tool-scoping all key off the composed modules, not a
  substrate branch — so a new substrate should slot in without forking the core.
- **The design lives in `docs/ir_architecture.md`** (substrates + mechanic-modules) — read/refresh it
  first; reconcile with what's actually been built since.
- **Current "movement" is still discrete:** the Godot walkable overworld (`overworld.gd`) steps
  cell-by-cell and fires on step-onto-cell; combat is `turn_based`. These *feel* live but the MODEL
  is discrete. Real-time = continuous time, per-frame update, simultaneous entities.
- **`combat` already declares unbuilt real-time models** — `quality_backlog.md` §5 notes
  `real_time`/`auto` combat models are "declared, unimplemented." Reconcile: is real-time combat a
  combat-module concern under the real-time substrate, or the substrate's job?
- **Engine implication:** real-time almost certainly **Godot-only** (Ren'Py can't drive a per-frame
  sim). The substrate likely forces `engine=godot`, like `combat` does.

## Guardrails
- **Reuse the module/aspect machinery.** A substrate is the execution model; mechanic-modules compose
  *over* it. Real-time must not become a parallel universe that re-implements content authoring —
  the same `Module`/`Check`/slot-guarded-create loop authors its content where possible.
- **Don't fork the core.** Add a substrate + route by it (engine selection, the runtime loop); the
  maestro loop stays substrate-agnostic (it already keys off modules, not a substrate string).
- **Authoring stays checkable.** Real-time rules (speeds, spawn rates, collision, win/lose) must be
  authored as content with detect→fix checks, not freeform — the loop's completion guarantee applies.

## T1 — Design reconciliation
- [ ] **Re-read `docs/ir_architecture.md`** `real_time_sim` design; reconcile with current code
      (the walkable overworld, combat models, presenters). Write down what's still valid vs stale.
- [ ] **Decide the boundary** between the substrate (continuous-time loop, entity update, input) and
      mechanic-modules (what the entities *do*). Especially: where does real-time combat live?

## T2 — IR model for real-time
- [ ] **Extend the IR** for a continuous-time game: entities with position/velocity/state, per-frame
      update/behavior rules, real-time input mapping, collision/interaction, timed spawns, win/lose
      conditions expressed continuously (not turn resolution).
- [ ] **Schema fragment** in `docs/game_ir.schema.json` (+ rationale in `game_ir_decisions.md`);
      extend `ir_assemble`/`ir_crossref` for the new refs.

## T3 — Substrate selection + routing
- [ ] **`spec["substrate"]` gains `real_time`** — the proposer picks it (spec_write catalog),
      `spec_tools` resolves it, and engine routing forces Godot (like `combat`).
- [ ] **Modules declare substrate compatibility** — which mechanic-modules work under real-time vs
      discrete (composition validation, like the realization/projectable checks).

## T4 — Godot real-time runtime
- [ ] **A real-time presenter / game loop** in the Godot runtime consuming the IR on `_process`/
      `_physics_process` — continuous movement, simultaneous actors, frame-driven rules — vs the
      current discrete step-on-cell `overworld.gd`. Register it in the `PRESENTERS`/substrate routing.
- [ ] **Real-time combat** if in scope — implement the declared `real_time`/`auto` combat models
      against this loop.

## T5 — Authoring + proof
- [ ] **Author the real-time content** through the module loop (speeds, spawns, behaviors as
      slot-guarded/demand-driven creates with checks).
- [ ] **A hand-authored showcase** (like `docs/examples/combat_game.json`) exercising a real-time
      game end-to-end, as the ground-truth + test fixture.
- [ ] **Tests:** substrate selection routes to Godot; the IR crossrefs resolve; the runtime loads +
      runs the showcase; content checks fan/fix correctly.

## Ordering
T1 (design reconciliation) before anything — the model decisions gate the rest. Then T2 (IR) → T3
(selection) → T4 (runtime) → T5 (proof). Large, Godot-only, sequenced. Coordinate with `combat`'s
unbuilt real-time models (`quality_backlog.md` §5) so they're built once, here.

## Parked
- Ren'Py has no real-time path — real-time games are Godot-only by nature; confirm no attempt to
  project them to Ren'Py.
- Physics depth (full 2D/3D physics vs simple kinematic) — scope at T2.
- Relationship to `hd2d` presentation (real-time in 3D) — likely composes, decide at T4.
