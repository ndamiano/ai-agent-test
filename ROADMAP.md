# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

---

## Current state

**Rebuild complete (merged to `master`):** the fixed pipeline DAG was ripped out and replaced with an agentic loop + human-gated frozen spec; work now continues on `master` and short-lived feature branches. See CLAUDE.md for the design and current layout, and `src/maestro/README.md` + `docs/game_creation_walkthrough.md` for an orientation and an end-to-end trace.

**Module set redesigned (done):** the modules now decompose by honest concern, not old pipeline stages. `premise` dissolved (the game concept is spec data — title + `concept`); `cast`→characters, `story` (arc + endings, absorbs the old outline), `scenes` (the node graph, no spine/npc boolean — its floor reads the presence of `story`), `world` (places, no exclusion: `world`+`scenes` compose). `economy`→`state`, an always-on wiring invariant (every declared flag/var/item needs a producer AND a consumer — use-it-or-cut-it), with the item catalogue split into its own `inventory` module. `goal`/`dialogue_npc`/`outline` deleted; winning is self-evident per mechanic. Stats/combat still deferred.

**Engine-agnostic Game IR (done):** the agent now emits JSON (the Game IR — `docs/game_ir.schema.json`, rationale in `docs/game_ir_decisions.md`), never raw Ren'Py. The old Ren'Py-text path (`_script.py`/`_pnc_script.py` stitches, regex checks, text-edit/normalize helpers) is deleted. Components decompose as characters + asset_manifest + `nodes` [+ `places`/`items`/`story`]; `ir_assemble` lifts them to one IR, `ir_crossref` hard-gates references, `ir_vn`/`ir_pnc` project to `script.rpy`. Both genres compile lint-clean on the real SDK (8.5.2). This kills whole error classes (escaping/speaker/indentation/dangling-jumps) by construction and makes validation a data walk. Next: a live-LLM end-to-end run on the local model.

**Agentic build system (`src/maestro/`)** — backend complete (Phases 0–6), validated by 426 unit tests + a real-SDK compile; live end-to-end run pending (needs the local LLM up).
- **Spec** (`spec.py`) — per-game contract: components with typed done-conditions, frozen flag, dependency order. Drafted fresh per game by the agent (`spec_tools.propose_spec`, climbable `prompts/propose_spec.txt`); no human-authored genre schema.
- **Human gate** — `freeze_spec` is the human's out-of-band approval (no freeze tool); `amend_spec` (reason mandatory) un-freezes to pause for re-approval. Build tools refuse until frozen.
- **Durable state** (`state.py`) — per-run dir `<working_dir>/runs/<run_id>/`: component JSONs (ids = compile filenames), structured scratchpad (replace-not-append), story state. Source of truth; the transcript is never memory.
- **validate** (`validate.py`) — closed typed check set (exists / count / distinct / each_has / refs_resolve / compiles) → failure list = the recomputed to-do. Empty vs frozen spec = done.
- **Executor** (`executor.py`) — non-LLM loop. Simple stages (premise/asset) take one stateless step; `node_scripts` runs a **stateful sub-loop per target check** (`pick_target` order: compile → count → structural → content). Rebuilds minimal context (incl. locked-upstream + node-graph view); completion decided by validate; milestone + progress events.
- **Build agent** (`agent.py`) — per-stage modes (`mode_premise` / `mode_asset` / `mode_node`) with scoped prompt + tool set. premise/asset use a stateless one-call decider; node writing uses `make_node_subloop` — a bounded tool conversation with its own working memory that drives ONE target check to green (budget-capped via MessageBuilder so context stays bounded). Locked components injected so the agent never hunts ids.
- **Tools** (`tools.py`) — write_component / write_node (fused with story-state delta) / edit_node (surgical single-snippet patch), read_component / read_story_state, generate_asset, validate, compile_renpy, update_scratchpad, request_review.
- **Story state** (`story_state.py`) — continuity bible (facts / entities / open threads / recent tail); snapshot not log; spine-tracked.
- **Run** (`run.py`) — `python -m maestro.run "<request>"` CLI: propose → freeze → build → project path.

**Ren'Py capabilities (`src/renpy/`)** — `compile_renpy` (the spine: artifact → launchable project + lint/distribute gate), `build` + image generation, script assembly with deterministic character staging (`ir_vn` spreads/dims sprites and swaps the speaker's expression per line) and node-attributed lint errors. (`fns.py` is now art-only — two-pass image generation plus asset-manifest backfills; the old per-stage *text* generators are gone, since the agent authors all content. Their prompts were mined into the agentic mode prompts.)

**Per-line emotions + style-matched backgrounds (done):** dialogue lines carry an optional `emotion`; `ir_assemble` derives a per-character `expressions` map (only emotions actually spoken), `fns.generate_images` runs two-pass character art — a neutral txt2img base, then low-denoise **img2img** expression variants off it (identity holds, only the face changes) — and `ir_vn` shows `char_<id>_<emotion> as char_<id>` so the sprite swaps in place. Missing/failed variants degrade to the neutral face via compile-time placeholders. Backgrounds moved off the photoreal Qwen/Anima model onto **WAI Illustrious (SDXL)** so scenes match the cel-shaded anime sprites (`txt2img_background.json`). Test on existing runs with `scripts/regen_run_assets.py <run_id>` (LLM-tags emotions on old `nodes.json`, regenerates art, recompiles — no full rebuild). Caveat: Illustrious renders creatures named in a background description as subjects, so background descriptions should stay environmental.

**Substrates + composable mechanic-modules (done; `docs/ir_architecture.md`)** — the way genres scale. A game = one **substrate** (execution model — `discrete_state` today; `real_time_sim` designed, not built) + a composed set of **mechanic-modules**. A `Module` (`maestro/modules.py`) bundles the components it owns + their schemas/skeletons/baseline/deps/tools/sub-loop/verbs; `compose()` unions the active modules into the one bundle the build reads, and the spec proposer picks those modules directly from the module catalog (`Module.resolve_modules` forces the foundation, expands `requires`, derives the engine) — there is no genre classifier or preset box. The old per-genre dispatch (`GENRE_COMPONENTS`/`_BASELINES`/genre `if`s across run/tools/ir_compiler) collapsed into the registry; the executor core never branches on genre. Existing **vn** + **point-and-click** retrofit as module compositions with byte-identical output (parity-gated by the suite). Engine **projections** register per `(engine, module_id)`, so a module's schema is shared while its renderer is per-engine; a module with no projection for the chosen engine fails the compile fast.

**Card game — wander-and-wager (done)** — the first *new* mechanic on the new model: `card_play` (a `matches` component: card_model ∈ {high_card, blackjack}, opponent, ante, payout) composed with navigation + NPC dialogue (the proposer picks `card_play`; its `requires` pull in the rest, and the web-only projection forces the engine). An overworld `play_match` action enters a match and resolves back via `node_end`, mirroring combat. Rules are engine-implemented in the **web** runtime (`runtime/engine.js` `runMatch`); `card_play` registers a web projection and no Ren'Py one, so a card game builds on web and a Ren'Py build fails fast — the schema-agnostic / projection-per-engine seam, concrete. Validated by unit + crossref + web-compile tests; live-LLM run pending.

**Inference** — unified path `PipelineAgent` / `make_llm_decider` → `MessageBuilder` → `OpenAICompatibleConnector`. Structured JSON output with fallback; repetition detection; `safe_history_content()`; streaming with empty-result detection.

**Platform** — tool manager (decorator, auto schema inference), WebSocket event bus, model category settings (`large`/`medium`/`small`).

**Frontend** — rebuilt chat-first (`frontend/`): a chat tab and a Games tab that browses runs, freezes a spec, kicks a build, and streams live build progress over the websocket.

**Human-in-the-loop build controls** — the human is a participant in the loop, not a spectator. Pause / resume / cancel a running build (`maestro/run_control.py` — a cross-thread signal the executor checks at step boundaries); hand-edit a component/node, regenerate assets, and compile on demand (all gated to a parked build); and arbitrate "done" — add human todos that block completion (the build parks in `awaiting_human`) and waive machine checks the human accepts (`maestro/hitl.py`). REST control surface on `/api/games/{run_id}/…`; status pushed over the existing websocket. Deferred: manual edits leave `story_state` stale → healed by a future post-compile refiner (see `docs/quality_todo.md` §8).

Remaining UX: optimistic mid-build interjection ("I don't like this") via a director's note / steer-next-target, `revise_component`/`fork_run`, chat-spawned builds, LLM quality-gate (see `per-stage-differentiation.md` Part D).

**TODO — close the chat↔games seam:** when a chat turn creates/amends a run (`propose_game_spec`/`amend_game_spec`), the chat gives no signal — no run_id, no link, no tab-switch — so the user must know to open Games and hit Refresh. Surface the new run inline in the chat reply (a "Spec ready — Review →" card / deep-link that selects the run in Games), and have the chat tool-result carry the run_id back to the client. Deferred in favour of improving the game detail page first.

**Pending** — live-LLM end-to-end validation; eval is trimmed to grading finished artifacts (`eval/cli.py score game`), hill-climb tooling to return later.

---

## Architecture decisions

### Agentic loop + frozen spec (replaces the pipeline DAG)
The agent drafts a per-game spec of components with checkable done-conditions; the human freezes it; a non-LLM executor builds against it until validate passes. Steals the pipeline's completion guarantee (done = artifact satisfies the spec) and no-context-rot (minimal rebuilt context each step) without the rigidity of fixed stages.

### Small model strategy
Decompose over one-shot. Every stage that produces N items loops (one call per item). Each call gets only what it needs — no full character objects where `id+role+personality` suffice. Show output skeleton before field descriptions. These principles apply to prompts and to the call structure itself.

### Local model hardening
Small local models fail in specific, detectable ways: repetition loops, channel markup in responses, streaming that silently returns nothing. Detect and handle each at the connector/agent boundary rather than letting failures propagate into pipeline state.

---

## Next — Composition and scale

- [ ] **Parallel subpipelines** — `run_subpipeline` currently sequential; queue/gather pattern within FnStages
- [ ] **Pipeline parameter schema** — agents know what inputs each pipeline expects before firing
- [ ] **Automated prompt optimization (to re-add)** — LLM-as-judge scorer + hill-climbing loop. Removed in the rebuild; eval currently grades finished artifacts only (`eval/cli.py score game`). Prompts are kept as swappable `.txt` files so climbing can return.
- [ ] **Per-character dialogue agents** — replace single dialogue call with an orchestrator + one agent per character. Each agent holds only their character's context. Long-term: try different models per character to match voice/capability to role. Validate via eval before/after comparison.
- [ ] **Game IR 0.2 — first-class `locations`** — today a VN `node.location` points straight at an `asset_manifest.backgrounds` id (1:1 place↔image; the background is generated from that entry's description). Promote location to its own entity `locations: [{id, name, description, mood?, music?, time_variants?}]` so a place can carry day/night background variants, ambient music, and mood; the background image(s) generate *from the location*, and `node.location` repoints from a bg id → a location id. Deferred: while a location only holds one description + one image it is pure indirection, and the field can't land cleanly while IR 0.1 is frozen. PnC `places[].background` should converge on the same location concept.

## Pipelines requiring composition

- [ ] **World building pipeline** — world bible → factions → nations/cities → characters. Each layer its own sub-pipeline. Eventual target: explorable artifact.
- [ ] **Long-form fiction / novel pipeline** — outline → chapters → continuity tracking across generations
- [ ] **Comic / manga pipeline** — story → scenes → panels → dialogue + images per panel
- [ ] **Music generation pipeline** — integrate with a music model (MusicGen, Suno API)
- [ ] **VR world pipeline** — see North Star below

## Accessibility
*Once quality and coverage are there, make it easy for everyone.*

- [ ] Auto-install and manage ComfyUI / LMStudio
- [ ] Model selection assistant (help user pick the right model)
- [ ] Plugin / contribution system for third-party pipelines and tools

---

## North Star — VR World Generation

*"I want to explore a world where magic is real in VR" → hours/days later, a playable world.*

**Pipeline stages:**
1. **World bible** (LLM) — magic system, history, factions, geography, tone, key locations
2. **World layout** (LLM + procedural) — regions, cities, dungeons, roads, points of interest as structured data
3. **NPC generation** (LLM, sub-pipeline) — who lives here, roles, dialogue trees, daily schedules
4. **Quest / story generation** (LLM) — main quest, side quests, random encounters, magic interactions
5. **Asset specification** (LLM) — enumerate every 3D asset needed: buildings, props, creatures, items
6. **Asset generation** (3D model) — mesh + texture per asset
7. **World assembly** (code generation) — Godot project files, scene layout, scripting, VR config
8. **Build + export** — Godot CLI build to VR-ready binary

**Hard dependencies:**
- Sub-pipeline support — NPC generation, world layout, asset generation are each their own pipelines
- Text-to-3D model — current quality (TripoSR, Shap-E) is rough but improving fast; this is the main blocker
- Godot integration — scenes, scripts, assets must be assembled programmatically
- VR headset build pipeline — OpenXR export via Godot CLI

**What's achievable today:** world bible, NPC dialogue, quest outlines, asset specification — all LLM stages.  
**The blocker:** 3D asset quality. Text-to-3D models are improving fast; revisit when output is usable.  
**Engine target:** Godot (open source, scriptable, OpenXR support, closest to Ren'Py in programmatic project generation).

---

## What doesn't need to change

- Tool manager decorator pattern + schema inference
- MessageBuilder context budgeting and deduplication
- Connector abstraction (OpenAI-compatible, streaming, swappable)
- WebSocket event bus
- `compile_renpy` spine + Ren'Py assembly/build
