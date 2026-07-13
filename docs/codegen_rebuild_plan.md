# Codegen Rebuild — Plan

Branch: `codegen-rebuild`. Foundation validated 2026-07-12 (see `runtime/`, memory
`project_codegen_pivot.md`). This plan turns the proof-of-concept into the product and demolishes
the IR path it replaces.

## North stars (locked)
- **Any game + local** (bent on "good"). Every generation runs on the 5090; no cloud in the build loop.
- **Fat kit thesis:** the model composes primitives; hard/ambiguous mechanics become kit calls, not
  hand-rolled code. Breadth = (# primitive families) × (∞ content skins), grown offline, run local.
- **Sim/render split is load-bearing:** `update()` is pure JS (headless-gated); render is the only
  engine-specific layer. Proven to carry 2D→3D with zero gradient change. Never violate it.

## Architecture (target)
```
request ──chat──▶ SPEC (design doc JSON) ──human freeze──▶ BUILD LOOP ──▶ playable folder
                   (stage 1, local model)                  (AgentLoop, retargeted)
                                                             │
                    author/patch games/<slug>.js ◀──────────┤ get_fix
                    against the kit (stage 2, local model)   │
                                                             ▼
                    GATES: headless smoke + probe invariants (pure-sim, local, no critic)
```
What survives from the old core: `AgentLoop`, `Module`/`Check`/`Error`, `Services`, spec-freeze
gate, asset pipeline, connector/llm_clients. What dies: the whole IR (`ir_assemble`, `ir_crossref`,
`game_ir.schema.json`), both engine backends (`renpy/`, `godot/`), every mechanic module, `tools.py`,
`map_builder`, `music`, story_state.

---

## Phase 1 — Productize the loop (make it real, delete scratchpad)
Goal: the validated flow lives in the real codebase, driven by `AgentLoop`, not throwaway scripts.

- **T1.1 Codegen tools.** New `maestro/codegen/tools.py`: `write_game_file(slug, code)`,
  `read_game_file(slug)`, `run_headless(slug)`, `run_probe(slug)` — wrap the node runners as tool
  callables returning structured dicts. Acceptance: callable from python, JSON verdicts match the CLI.
- **T1.2 Two modules.** `SpecModule` (stage 1: one `Check` — spec exists + minimally shaped; fix =
  draft via `spec_draft.txt`) and `CodegenModule` (stage 2: checks = `authored` [file exists],
  `runs` [headless ok], `plays` [probe ok]; fix = author/patch against the kit). No loop edits —
  reuse `get_errors`/`get_fix`. Acceptance: `AgentLoop` drives request→game with these two modules.
- **T1.3 Prompts as files.** `maestro/codegen/prompts/`: `spec_draft.txt`, `author_game.txt`,
  `fix_game.txt` (port the scratchpad system prompts; keep `kit_api*.md` as the injected surface).
  Hill-climbable per the repo rule. Acceptance: no inline prompt strings.
- **T1.4 Run entry.** `maestro/codegen/run.py` — `create_run` + `run_build` for the codegen path;
  CLI `python -m maestro.codegen.run "<request>"`. Acceptance: one command → playable `runs/<id>/game/`.
- **T1.5 Delete scratchpad harnesses** once T1.1–T1.4 replicate them. Acceptance: `codegen_test.py`,
  `fix_round.py`, `e2e.py`, `e2e3d.py` gone; parity proven on all 5 sample games.

## Phase 2 — Demolition (rip out the IR)
Goal: remove the dead architecture. Do AFTER Phase 1 proves the replacement.

- **T2.1 Delete engine backends** `renpy/`, `godot/` and `maestro/engines.py`.
- **T2.2 Delete the IR** `maestro/ir_assemble.py`, `ir_crossref.py`, `docs/game_ir*.md/.json`,
  `depgraph.py`, `story_state.py`.
- **T2.3 Delete mechanic modules** `maestro/modules/{scenes,story,world,combat,cast,inventory,
  bible,objectives,assets,state,human,aspects,wild_encounters}.py` + `checks/context/views/module`
  reductions — keep only the `Module`/`Check`/`Error` ABC + registry.
- **T2.4 Delete** `tools.py`, `map_builder.py`, `music.py`, `asset_stubs.py`/`asset_prompts.py`
  (until re-hooked in Phase 6), `climb.py`, `rewrite.py`, spec_tools' IR bits.
- **T2.5 Fix fallout** — imports, `spec.py`, tests. Acceptance: `pytest` green, no dead imports.
- **T2.6 Docs** — rewrite `CLAUDE.md` architecture section + `docs/ROADMAP.md` to the codegen world.

## Phase 3 — Harden the gate (climb valid→playable)
Goal: the probe catches more real brokenness, per genre-agnostic invariants.

- **T3.1 Camera/scroll invariant** — if the sim implies a world larger than the screen (entities
  travel beyond `config.width/height`), require a camera that moves. (Fixes the platformer gap.)
- **T3.2 Reachability/softlock** — with scripted exploration, assert the win state is reachable and
  the player is never permanently stuck (no-progress-possible detector).
- **T3.3 Progress invariant** — score/state actually advances under reasonable play (not just "runs").
- **T3.4 Per-genre invariant packs** — a small registry so a spec's genre pulls extra checks
  (platformer: player never below the floor; collectathon: collectibles reachable).
- **T3.5 Actionable diagnosis discipline** — every violation names the likely cause + the kit
  primitive to use (the units-warning pattern that fixed the platformer in 1 round).

## Phase 4 — Widen the kit (buy genres)
Goal: each primitive family unlocks a game family. Build offline (frontend model), run local.

- **T4.1 Pathfinding/steering** → tower defense, chase AI, stealth. (grid A* + seek/flee.)
- **T4.2 Grid/turn substrate** → roguelike, tactics, match-3, sokoban.
- **T4.3 Particles + juice** → feel primitives (screen shake, hit-stop, tweens) — cheap quality.
- **T4.4 3D physics** — `physics3` (gravity + ground + collide) mirroring the 2D helper; a flyer
  primitive (thrust/pitch/yaw) → the flight-sim-ponies callback.
- **T4.5 Audio backend** — wire `kit.audio.play` to real sfx (procedural or asset), fail-soft.
- Each family: kit code + `kit_api` doc section + a worked example in the prompt + a probe invariant.
  Acceptance per family: local model authors a new genre in ≤5 rounds.

## Phase 5 — Assets (skin the shapes)
- **T5.1 Sprite stubs from the sim** — derive the asset set from entity shape tags (the
  reconcile-by-construction idea survives; retarget it off entities, not IR).
- **T5.2 2D sprites** (DONE) over placeholder rects/circles: `reskin.py` plans a sprite set, rewrites
  draw to prefer `kit.sprite(id)` w/ shape fallback, re-gates, renders (ComfyUI).
- **T5.3 3D meshes** (DONE) for `box/sphere` entities: `reskin.py` (mode-dispatched) plans meshes,
  tags entities `mesh:"id"`, renders an image (ComfyUI) → GLB (TRELLIS); `engine3d` preloads the GLBs
  (vendored GLTFLoader) and scales each to its entity box, primitive-shape fallback. Additive.
- **Render res** (DONE) — bumped 2D→960×540, 3D→1280×720, canvas fills the browser window (aspect-
  preserved letterbox); pointer maps back to game coords.
- **T5.4 Styled-prompt stage** — reuse the authored-description + brief → prompt template idea.

## Phase 6 — Frontend / UX (chat → freeze → build → play)
- **T6.1 Chat drafts the spec**, renders it for human review/edit at the freeze gate (reuse the
  existing spec-freeze UI pattern).
- **T6.2 Build progress** — stream rounds/gate verdicts to the UI (reuse the event bus).
- **T6.3 Play in-app** — serve the built `game/` folder; iframe the runtime. Save/share later.
- **T6.4 Per-game HITL edits** — "make the enemies slower" → a targeted patch round.

## Phase 7 — The "good" tier (deferred moonshot)
The bent constraint. Not now, but the slot exists.
- **T7.1 Self-play metrics** — solvable/non-trivial/fair as a difficulty proxy (computable, local-ish).
- **T7.2 Play-critic** — vision+control agent judging fun/feel (frontier, cloud) — the thing that
  turns "playable" into "good". Explicitly out of local scope; a future cloud-assist mode.

---

## Sequencing & risks
- **Order:** 1 → 2 → 3, then 4/5/6 in parallel (assets before frontend — a skinned game validates
  via the runtime harness; the in-app UX is scaffolding). Never demolish (2) before (1) is proven.
- **Risk: coherence ceiling.** Bigger games exceed a 30B's one-file coherence. Mitigation: decompose
  authoring (state→systems→render as separate steps) when one-shot's failure rate climbs — measure
  first, don't pre-build.
- **Risk: gate gives false green** (passed a broken map / no-camera game). Mitigation: Phase 3 is
  continuous — every observed "passed but bad" becomes a new invariant.
- **Risk: kit surface bloats** past what fits a prompt. Mitigation: per-genre kit doc slices; inject
  only the families the spec's genre needs.
- **Definition of done (v1):** `python -m maestro.codegen.run "<any small game>"` → a folder that
  runs in a browser, for ≥8 distinct genres, ≥80% reaching playable in ≤6 local rounds.
