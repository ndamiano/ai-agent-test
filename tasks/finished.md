# Finished — shipped work ledger

Append-only. When a **section** of a task file goes all-`[x]`, cut it from the active file and
drop a one-line entry here (newest at top). Keeps active task files free of dead checklists while
preserving the record. One line each — git has the detail.

Format: `- **YYYY-MM-DD** — <what shipped> (<key files/settings>)`

---

## Drained sections (2026-07-25)

Cut from their active files after a per-file audit against HEAD; the work is in the code, the
plan text was dead weight.

- **2026-07-25** — `auth_and_billing.md` + `build_deploy.md` + `scaleout.md` + `production_hardening.md` merged into `platform_polish.md`: each had shrunk to 3–4 open items behind its own Why/Background/Guardrails, and two owned the same queue-fairness task
- **2026-07-25** — `tasks/build_loop.md` opened: the codegen loop had no task file (it lived in `docs/codegen_rebuild_plan.md`, which has 0 checkboxes and names `AgentLoop`/branch `codegen-rebuild`/a `draw` hook — history, not a plan)
- **2026-07-25** — every open item across `tasks/` given a falsifiable `→ done when:` check + a `Verified:` stamp per file; lifecycle rule changed to prune-on-land (README)

- **2026-07-25** — auth_and_billing T1/T2/T3/T5 + the compute-budget layer: login gate on every route, run ownership, credit ledger (charge-once, manual refund), frontend login+balance, `SECONDS_PER_CREDIT` reserve/admit/debit (`src/auth/`, `src/db/store.py`, `frontend/src/contexts/AuthContext.tsx`)
- **2026-07-25** — build_deploy T1 suite+frontend CI, T2 backend image/compose, T3 persistence (`data_dir` sqlite + named volumes), T4 alpha deploy (`.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`, `scripts/deploy.sh`)
- **2026-07-25** — scaleout S1 build queue/WS routing/chat isolation, S2 parallel assets, S3 backend abstraction + runpod scale + cost metering (`src/db/`, `src/worker/`, `src/scaler/`, `codegen/asset_chain.py`)
- **2026-07-25** — safety_filter Phase 1 research + the pre-alpha basic block: input screening (chat + spec) and pre-gen image-prompt screening, logging with attribution (`src/tools/safety.py`, `tests/test_safety.py`)
- **2026-07-25** — production_hardening H2 chat cap (30 turns/hour, 429) + H3 API surface (`MAESTRO_DEV`-only docs, 7d session TTL) (`src/auth/ratelimit.py`, `src/api/app.py`, `src/auth/store.py`)
- **2026-07-25** — legal_ops private-alpha tier: operator-favorable terms + privacy note, 18+ affirmation on login (`frontend/public/terms.html`, `privacy.html`)
- **2026-07-25** — doc_accuracy T1 (the 2026-07-08 audit) retired: it covered the pre-codegen tree; replaced by a fresh post-rebuild pass with the drift already located

## Retired workstreams (2026-07-23)

- **2026-07-23** — storyline_pivot.md retired: plan targeted the deleted IR story/scenes stack; the reusable narrative-design conclusions (theme+tone spine, graph-of-linear-storylines, code-guarded termini, single-ending open worlds) distilled to `docs/narrative_design.md`
- **2026-07-23** — pre_alpha_kickoff.md retired: complete — all 4 pieces shipped (auth+ownership, build queue, per-user WS routing, credits stub) and validated end-to-end by a non-owner on a second box
- **2026-07-23** — realtime_substrate.md retired: obsolete — `runtime/engine.js` already IS the real-time frame-loop engine, the substrate-build plan is moot
- **2026-07-23** — hitl_backlog.md retired: all-checked-off record built entirely on deleted plumbing (agent_loop, ir_crossref, depgraph)
- **2026-07-23** — module_catalog.md retired: Module-catalog concept gone; mechanics are now kit primitives + authored code
- **2026-07-23** — aspects_and_scale.md retired: aspect/Module-catalog layer has no codegen analog

## Codegen rebuild + platform

- **2026-07-23** — build-as-jobs: resident AgentLoop deleted; a build is a completion-driven chain of `llm` jobs off a durable cursor (`build_chain.py`, `build_state.py`, `build_steps.py`, reaper re-advance)
- **2026-07-23** — TRELLIS cold start cut ~330s→116s (+ steady ~30%): /dev/shm checkpoint staging, suppressed default init, tier-scoped loads, warm-up gate before register; 50k-tri decimation (`tools/trellis_server.py`)
- **2026-07-22** — compute-budget metering: GPU work admitted against grant − used − reserved estimates in one txn; only delivered work billed; overdraw bounded not killed (`db/store.py` enqueue_job, `db/estimates.py`)
- **2026-07-22** — control scaffold + worldgen seeds, merged as orthogonal pre-seeds: per-scheme GENERATED main.ts owns config/movement/collision, worldgen owns the place (town + wilderness ring), model authors game.ts hooks (`codegen/scaffold.py`, `worldgen_bridge.py`)
- **2026-07-22** — data-file stage: model designs per-game datasets once, deterministic row validation + typed data.ts regen; a row's envelope owns its whole visual so the skin stage needs no source rewrite (`codegen/data_files.py`)
- **2026-07-21** — worker-pull queue + RunPod autoscaler validated end-to-end on prod: llm/image/mesh queues all pull-side, workers own scale-down (idle self-exit), control plane owns scale-up + pod reaping (`db/`, `worker/`, `scaler/`)
- **2026-07-15** — IR→codegen rebuild (Phases 1/2/6): codegen build loop productized + IR path demolished (07-12), frontend+API retargeted to codegen (07-15) — TS games against the fat kit, gates decide done (`maestro/codegen/`, `runtime/engine.js`)
- **2026-07-07** — pre-alpha kickoff complete: auth + game ownership + credit ledger (charge-once, manual refund) + single-GPU build queue + per-user WS routing; non-owner drove the full loop on a second box (`src/auth/`, `src/db/`)

## Build system + engines

- **2026-07-05** — 3D gen model: Hunyuan3D-2.1 image→mesh, then TRELLIS.2-4B won the bake-off (native PBR, MIT); selectable `settings.comfyui.mesh_backend`, TRELLIS standalone server `tools/trellis_server.py`
- **2026-07-05** — 3D spec-selectable: proposer authors `presentation` (2d/hd2d); `_resolve_presentation` forces godot on hd2d+world; mesh gen gated on hd2d
- **2026-07-05** — HD-2D 3D presenter: `godot/runtime/overworld3d.gd` renders the same game.json in true 3D (presentation-neutrality proof)
- **2026-07-05** — progression decoupled from combat: `level_var`/`per_level` IR primitive (any effect feeds the pool); `wild_encounters` extracted to a thin selectable module
- **2026-07-04** — depth: progression + wild encounters (IR `progression`, `combatant.xp_yield`, `place.encounter_table`; persistent player combatant carries damage/XP/levels across fights + saves)
- **2026-07-04** — walkable Godot overworld: WASD/arrow tile grid, `map_builder.py` deterministic rasterize (region plan → tiles/anchors/footprints), presenters registry keyed on `place.kind`, ideogram4 tile pipeline, combat battle UI, player chrome (title/pause/save/ending/inventory)
- **combat authoring module + Godot auto-routing** — `maestro/modules/combat.py`, one slice per step in dependency order; `(godot, combat)` projection auto-selects Godot via `Module.engine_for`
- **parallel fixes** — slot-guarded creates batch up to `parallel_fixes` concurrent LLM calls, each pinned to its own slot; tool writes serialize on a lock

## Generation quality + prompts

- **outline / beat-sheet stage** — outline component between premise and nodes: logline + ordered beats (purpose + tension) + an `ending_paths` entry per premise ending; ≥5 beats forced; injected into node authoring context
- **prompt DRY** — `{{include:NAME}}` partials in `render_template` (`tool_call_rule`, `conditions_ref`, `effects_ref`, `derive_from_request`); skeleton shape single-sourced from `SKEL_*`
- **spec prompt single-source** — one `propose_spec.txt` generated from the composed module set; `propose_spec_pnc/card.txt` deleted; baselines are the single contract source
- **tool gating onto `Module`** — `mode_tools`/`mode_prompt`/`prompts`/`target_*` moved off `agent.py` globals; adding a module needs no `agent.py` edit
- **context-bloat trims** — per-mode `skeleton_guide` scoping, upstream trimmed to used fields, SPEC block scoped/dropped in the render path
- **born-compliant write fixes** — VN character floor relaxed to min 2; `each_node_has_location` check; `classify_genre` emits `card_ante`; narration-alias speakers normalized to null at write time
