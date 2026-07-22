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

- **T1.1 Codegen tools.** New `maestro/codegen/tools.py`: `write(slug, code)`,
  `read_file(slug)`, `run_headless(slug)`, `run_probe(slug)` — wrap the node runners as tool
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

## Data files — content out of code (new stage)
Goal: game DATA (enemy stats, item tables) lives in per-run data files, not inline in authored .ts.

Three problems, one root — content rides inside code. Adding an enemy is code surgery. Asset
planning (`reskin.plan_assets`) has to LLM-guess the "visually distinct kinds" from source and caps
at 3–8 sprites. Big content (a 30-enemy roster) blows the fix-loop context because rows ride inside
the file bodies. Segregation fixes all three: enumeration becomes deterministic (rows ARE the
catalog), context collapses (the fix loop sees schema + ONE example row, not 30 stat blocks — the
same trick worldgen's GENERATED-world.ts exclusion already proves), and content changes become row
edits. The model DESIGNS its own shapes per game — an Isaac enemy and a debate-sim opponent share
nothing; one universal schema would be per-genre generators sneaking back in — but the shape lands
on disk as a machine-checkable artifact, so a deterministic gate validates rows and a generator
emits typed code. Envelope principle: universal fields serve the PIPELINE, custom fields serve the
game; the envelope is nullable because the asset pipeline is additive (no look ⇒ shapes, so nothing
can break).

**Design (locked):**
- Per run: `game/data/manifest.json` = dataset declarations
  (`{"datasets":[{"name","fields":{...}}]}`); `game/data/<name>.json` = rows (flat objects). Both
  written by a new human-free build stage.
- **Envelope** on every row, implicit — never declared in fields: `id` (required slug), `name?`,
  `look?` (art prompt), `presence?` (`"world"|"ui"|"both"` — the asset directive: world→sprite/mesh,
  ui→icon, both→both), `size?` (`{w,h[,d]}`). Non-visual datasets (status effects) simply omit
  look/presence.
- **Field type vocab:** `number | string | boolean | number[] | string[] | ref:<dataset> |
  ref:<dataset>[]`, trailing `?` = optional. Rows stay flat; refs buy cross-dataset integrity checks.
- **`game/data.ts` is GENERATED** deterministically from the JSON (marker line; regenerated whenever
  the JSON changes; protected from rewrite the same way world.ts is): one interface per dataset
  (`EnemiesRow`) + one typed const (`ENEMIES`). Games import from `./data.ts`. tsc typechecks the
  data natively (kills the JSON-invisible-to-tsc hole), esbuild bundles it, NO engine/kit/runtime
  change, sim/render law untouched.
- **New blocking check `data`** on `CodegenModule` between `planned` and `authored`. Design missing →
  one-shot LLM (`design_data.txt`) designs datasets + rows (`{"datasets":[]}` is legal — simple
  arcade games opt out; unparseable output falls back to the empty design). Design present →
  deterministic `validate_data` (shape/type/id/ref/envelope violations as FIX errors, fixed by a
  one-shot rows rewrite via `fix_data.txt`) + regenerate data.ts.
- **Prompt integration:** `plan_game.txt` is told content tables are NOT files to plan;
  author_file/fix_loop get a compact GAME DATA summary block (dataset, const name, fields, row
  count, ONE example row) + the rule "import from ./data.ts, never re-declare tables inline".
- **Asset stage:** `sprite_plan_from_data` replaces the plan_assets/plan_meshes LLM call whenever
  any row carries `look` — deterministic enumeration (2D: all look rows; 3D: look + presence
  world/both), prompts from `look`, sizes from `size`. LLM planning stays as the fallback for
  data-less games. This removes the 3–8 sprite ceiling for data-driven games.

**Implementation plan (ordered):**
- **D1 Substrate.** New `maestro/codegen/data_files.py` owns validate/generate/summary/sprite-plan:
  parse + validate manifest and rows (type vocab, envelope, id/ref integrity), emit data.ts,
  build the GAME DATA summary block, enumerate the sprite/mesh plan from rows. Pure functions.
- **D2 The check.** `module.py`: blocking check `data` between `planned` and `authored`;
  `_design_data_fix` (one-shot design via `design_data.txt`, empty-design fallback); validation
  failures dispatch the one-shot rows rewrite (`fix_data.txt`); inject the GAME DATA summary into
  `_author_file_fix` + `_read_write_loop_fix`.
- **D3 Prompts.** New `prompts/design_data.txt` + `prompts/fix_data.txt`; tweaks to `plan_game.txt`
  (tables are not files), `author_file.txt` + `fix_loop.txt` (import from ./data.ts, never inline).
- **D4 Assets.** `reskin.py`: route through `sprite_plan_from_data` when any row carries `look`
  (both modes); keep the LLM plan as the data-less fallback.
- **D5 Tests.** `tests/test_data_files.py` — validate (good/bad types, dup ids, dangling refs,
  envelope), generation (interfaces + consts, marker, regen-on-change), summary shape, sprite-plan
  enumeration for 2D/3D.

**NOT doing:**
- No `kit.data` runtime primitive — imports beat runtime loading; the bundle already carries the data.
- No universal/per-genre schema — the model designs the shape per game, the envelope is the only
  universal surface.
- No `tools.py` JSON editing in v1 — the data fix rewrites the rows file whole.
- No back-compat paths: old runs simply have no `game/data/` dir and every consumer falls back
  (LLM asset planning, no summary block, no data.ts).

**Scale caveat:** data files fix enumeration + context, not generation cost. At hundreds of rows
(Skyrim-scale content) authoring the rows becomes its own looped stage (rows per category) — out of
scope here; this substrate is that stage's prerequisite.

## Control scaffold — the pipeline wires controls (new stage)
Goal: the control layer of every non-world game is GENERATED from the frozen spec's control scheme,
never model-authored.

Two live builds shipped games where opening them did nothing. One never read a movement key at
all — the probe's dead_controls passed because a space-attack mutated state ("some key changed
something" read as live controls). One wired kit.moveTopDown correctly and then a hand-rolled
collision loop undid the movement every frame. Same root: controls are the one part of a game with
exactly ONE correct realization given the frozen spec's scheme, so model-authored glue there is
pure downside — every LLM call spent on it can only preserve or break a known-correct answer. This
generalizes the worldgen precedent (pipeline-seeded world.ts) to the control layer.

**Design (locked):**
- `seed_scaffold(state, spec)` (`maestro/codegen/scaffold.py`), called from `run_build` right after
  the worldgen seed, for every game EXCEPT world-flagged ones. Idempotent like the worldgen seed:
  an existing main.ts (scaffold on re-entry, or a pre-scaffold run's entry) ⇒ no-op.
- Writes `game/main.ts`, first line `// GENERATED control scaffold …` — the same edit-refusal
  convention as data.ts / worldgen's world.ts (and `edit` now refuses ANY file whose first line
  starts `// GENERATED`, with the header line as the pointer to the owning source).
- Content renders from a per-scheme template (`scaffold_templates/<scheme>.ts.tmpl`, real
  TypeScript we own — hill-climbable; unknown/absent scheme → `default.ts.tmpl`, no movement
  wired). The scaffold owns: deterministic `config` (2D size defaults; 3D `controls:` name baked
  per template — orbital/vehicle/fp/follow), `createGame`, the scheme's movement — top-down →
  `kit.moveTopDown`; platformer → the kit-doc walk/jump/physics idiom (state.tilemap or
  state.solids, state.gravity); grid-turn → `kit.gridMove` on pressed keys with a
  `state.passable(cx,cy)` contract (absent = all passable) + `state.moved` for the enemies' turn;
  any `-3d` → `kit.drive` — and delegation to the hook module the model authors: `game.ts`
  exporting `createState(kit)`, `init(state, kit)` (MUST set state.player — the scaffold's init
  asserts it with a named throw the headless gate surfaces), `update(state, dt, input, kit)`,
  `draw(g, state, kit)` (2D only), `hud(state, kit)`. The player's `speed` rides the entity so
  data rows can set it. Movement runs BEFORE the hook update — the ordering contract is
  docstringed in the template: gameplay adjusts/clamps after (push OUT of an overlap), never
  re-wires or undoes the move.
- Dialogue: when the spec's `uses` names dialogue (the conservative v1 trigger), the scaffold also
  owns the whole talk loop — `kit.talkStep` every frame, E opens the nearest `talk`-carrying
  entity within 48px/3u, a completed choice lands in `state.talkPick` for the hook update,
  `kit.talkHud` spread into hud, movement paused while talking.
- Planner/authoring integration: `plan_game.txt` plans `game.ts` (+ system files), never main.ts;
  `planned` requires game.ts in the manifest for scaffolded runs (keyed on the scaffold being on
  disk — production-equivalent to spec-keying since run_build seeds before the loop detects, and a
  hand-assembled main.ts run degrades to the old flow); `_plan_fix` drops a planned main.ts and
  falls back to a game.ts-only manifest; authoring order keys entry-last on game.ts;
  `author_file.txt` carries the exact hook signatures + the never-wire-movement /
  never-undo-movement rules; `fix_loop.txt` marks GENERATED files off-limits.
- Probe, scheme-aware: `run_probe(run_dir, scheme)` rides the frozen scheme into probe.mjs; when it
  names a movement scheme (top-down/platformer/grid-turn/any `-3d`) the probe adds
  **dead_movement** — under each held directional key (WASD/arrows) some entity must displace vs
  the no-input baseline; a state-mutating action key can no longer green a game the player can't
  steer. dead_controls is unchanged for non-movement schemes.

**Implementation (landed in this order):**
- **C1 Probe.** engine.js probe takes `scheme`; dead_movement invariant; probe.mjs argv;
  `run_probe(run_dir, scheme)`; `_detect_plays` passes the spec's scheme.
- **C2 Substrate.** `scaffold.py` (seed_scaffold/is_scaffolded/scheme_of) + `scaffold_templates/`
  (7 schemes + default + 2 interact partials), seeded from run_build.
- **C3 Module/tools.** `planned`/`_plan_fix`/`_authoring_order` keyed on the scaffold; `edit`
  refuses `// GENERATED` files (the data-check fix path is unaffected — it writes JSON directly,
  not through tools).
- **C4 Prompts.** plan_game.txt rewritten for the hook shape; author_file.txt hook-contract block;
  fix_loop.txt GENERATED rule.
- **C5 Tests.** `tests/test_scaffold.py` — per-scheme rendering, idempotence, world exclusion,
  the player-contract throw, dead_movement fires/passes, planned/authoring integration, and the
  scaffolded AgentLoop end-to-end through every real gate.

**NOT doing (v1):**
- World games keep the worldgen flow — it already prescribes the drive wiring and is validated
  end-to-end; folding it into the scaffold is a later unification.
- No 2D feel-tuning knobs beyond `entity.speed` (jump/gravity ride the same entity/state fields
  with kit-doc defaults; a feel pass is its own stage).
- Interact only on the conservative `uses`-contains-dialogue trigger — entity-desc sniffing
  invites false positives; widening is a one-line change in `_wants_interact`.

## Collision + actions — two kit widenings behind the scaffold (landed)
Two more live incidents, same root as the control scaffold's: correctness the model was trusted to
hand-roll. A shipped game had a `solidAt()` lookup that was never applied to movement (the knight
walked through walls) and enemies that stacked under the player (no pair separation) — both are ONE
missing collision pass. Another shipped a full melee implementation behind a key read that never
fired — the binding existed only as prose in the spec, invisible to every gate.

**Solid collision (2D):**
- `kit.collideWorld(world, solidAt?, cell=32)` (engine.js) — the ONE pass, both response cases:
  entity-vs-tile full pushout (minimal axis via resolveAabb, blocked velocity zeroed — the same
  resolution as platformer physics) and symmetric half-and-half pair separation on the minimal
  axis. Participants = entities tagged `solid: true` (not dead, real AABB). Broadphase = sort-by-x
  sweep; deterministic (stable order, no randomness). Pairs resolve first, tiles LAST so walls win.
- Scaffold: the 2D movement templates (top-down, platformer, grid-turn) call it AFTER the hook
  update — order is law: movement → gameplay → collide, so gameplay can't undo the resolution.
  `state.solidAt`/`state.cell` join the scaffold-read contract (optional; default to
  `state.tilemap.solidAt`/`tilemap.tile`; absent ⇒ pair separation only; grid-turn is pair-only —
  cell walkability belongs to gridMove's passable). 3D templates untouched: no 3D solid primitive
  exists (documented law).
- Probe teeth: `solid_overlap` (no two solid AABBs interpenetrate at rest, 2D only) and wall_clip
  now also fires via the `state.solidAt` contract (solid entities only), not just a state tilemap.

**Action registry:**
- `kit.register(name, keys, fn)` / `kit.bindings()` (engine.js) — named key-press actions, keys
  normalized like the key listener (single chars lowercased). Every runner (run, run3d, simulate,
  probe/render/scroll loops) fires handlers on the PRESSED edge of any bound key, update-side,
  AFTER the game's update for the frame; re-register by name REPLACES (a re-init never
  double-fires). Registration makes spec bindings machine-readable: the probe presses them, and a
  future frontend remap becomes an engine-level key→action indirection with no game-code change
  (NOT built now — just not precluded).
- Probe: `dead_action` — every registered binding is pressed in pulses and must produce a state
  delta vs the no-input baseline (non-positional mutation, spawn/despawn, or displacement);
  registered keys also join the controls-live mash. `unbound_control` — the spec's whole `controls`
  map rides gates.run_probe → probe.mjs argv (module._detect_plays passes ALL entries); the probe
  skips movement/mouse entries via EXPLICIT lists (the scheme's own keys — platformer's space-jump
  included — never inference) and flags any remaining key no registered action listens to, naming
  the register call to add. `dead_action` explicitly skips the scaffold-owned `"interact"` — its
  effect is proximity-gated (no talker near spawn ⇒ legitimately no delta), and its wiring is
  generated, known-correct.
- Scaffold interact: split in two — `talkStep` stays update-side (per-frame advance/choose/close
  key reads), the OPEN half moves to a registered `"interact"` action in the scaffold's init
  (fires after update; guards on `state.talk` so the two halves share E without double-handling).
- `author_file.txt` rule: every non-movement spec control is wired via `kit.register` in init;
  bare `input.pressed` for a spec-bound EDGE action is rejected by the probe (held mechanics still
  read `input.down` per frame).

**NOT doing:** no mass/physics materials (symmetric half-push only), no 3D solid collision, no
remap UI (registration just keeps it possible), no per-genre collision variants.

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
