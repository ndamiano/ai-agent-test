# Maestro

An AI platform that makes things. The goal is simple: user says "make me a game", an hour later a good game exists. The AI quality is the product — everything else (UI, install experience, visuals) is scaffolding that can be improved later.

The north star is magical output. A novel that rivals Dostoevsky. A game worth sharing. Not "technically completed" — actually good.

See `ROADMAP.md` for the full plan and current status.

---

## How it works

The user talks to Maestro via a chat interface. When asked to make something, Maestro does NOT write the artifact by hand and does NOT run a fixed pipeline. It drafts a per-game **spec** — a contract of components, each with checkable done-conditions — for the human to review and freeze. Once frozen, a non-LLM **executor** drives an agentic loop that builds the artifact against the spec until every done-condition passes.

**The agentic loop + frozen spec is the key architectural idea.** Three layers:
1. **Spec layer** (agentic, human-gated): the chat agent drafts a spec; the human reviews, edits, and freezes it. Build tools refuse until frozen.
2. **Executor** (`maestro/agent_loop.py`, NOT an LLM): drives the loop. Each step rebuilds a *minimal* context from durable on-disk state (spec + the modules' to-do + scratchpad + story state + last result) — the transcript is never used as memory, so context stays ~constant as the game grows. Completion is decided by the modules' `get_errors` (minus the human's waivers), never by the agent claiming done.
3. **Tools**: the bounded capabilities the agent composes (write_component / write_node, validate, compile_renpy, generate_asset, ...). The agent chooses the order.

It steals the old pipeline's two good properties (completion guarantee, no context rot) without its rigidity: "done" = the artifact satisfies the frozen spec, not "all stages ran."

---

## Architecture

```
src/
  agents/       MainAgent (chat persona — drafts/amends specs) + agent_store, chat.json / summarizer.json
  api/          FastAPI routers (chat, games, settings, agents, outputs, system, websocket)
  config/       settings_schema.py (Pydantic), settings_manager.py (singleton)
  llm_clients/  connector_selector.py, openai_compatible_connector.py, message_builder.py
                inference.py — PipelineAgent, call_llm, json_with_correction (shared inference primitives)
  maestro/      The agentic build system:
                spec.py — Spec (modules, params, story_state_schema, frozen flag)
                state.py — RunState: durable per-run dir <working_dir>/runs/<run_id>/
                modules/ — THE module system (one `Module` ABC, no subclasses; see below):
                  module.py — Module ABC + Check + Error/ErrorType/CorrectionPrompt + MODULE_REGISTRY +
                    compose() + Preset/PRESETS + the (engine,module) projection registry +
                    load_prompt/skeleton_guide. A module IS a list of `Check`s (each a detector +
                    how-to-fix); the base runs them — `get_errors` sweeps the checks, `get_fix`/
                    `get_correction_prompt` build the fix for an emitted error from its check.
                  checks.py — the check library a module's Check detectors compose (path-addressed
                    primitives + structural graph checks + crossref/compile wrappers)
                  context.py — Context (durable per-step snapshot) + render_dict (the run-state
                    frame + raw artifact); views.py — the shared graph/condition GRAMMAR only
                    (nodes_of/reachable/move_targets/cond_items...), no projections.
                    A MODULE IS THE ONE-STOP SHOP FOR ITS DOMAIN: it owns its checks (policy
                    detectors live in the module, checks.py keeps only generic primitives +
                    crossref/compile wrappers), its write-time tool policy (scenes.node_write_error,
                    world.action_error, card_play.match_write_error — tools.py just dispatches),
                    its graph projection (scenes.node_view / world.place_view), its slot policy
                    (a guard dict carries the module's assign/prepare/cap callables — the loop and
                    services know no module's view shape), AND its presentation block — how its
                    component appears in OTHER modules' prompts (cast.character_cards,
                    assets.locations_block, inventory.items_block, story.story_block,
                    scenes.nodes_index_block, world.places_index_block, combat.combat_index_block).
                    There is NO generic upstream/component dump: every module's render_context
                    CRAFTS its own prompt from the raw artifact by composing sibling modules'
                    blocks + the run-state frame (maestro/context_render.py holds ONLY that frame)
                    — the consumer knows what its call needs and owns the window budget that
                    implies (scene text never enters a prompt except the assigned slot's lead-in).
                  cast/story/scenes/world/assets/inventory/state/card_play/combat/human.py — the
                    mechanic-modules, each a direct Module subclass; each owns its component's
                    structural write-time validator + authoring skeleton INLINE (no shared
                    validators/skeletons file). state/human author no component (cross-cutting):
                    state is the always-on wiring invariant, human holds the HITL todo/waiver store.
                agent_loop.py — AgentLoop, the non-LLM loop that DRIVES the modules (not itself a
                  module): collects each module's get_errors, subtracts the human's waivers,
                  prioritizes by error TYPE (human>build>fix) then Module.priority then the check's
                  DECLARED order (`Module.check_rank` — author before wire before polish), asks the
                  module for a Fix and runs it. When the top error is a slot-guarded create, up to
                  `parallel_fixes` (settings) same-code siblings run CONCURRENTLY — one thread per
                  fix, each with its own slot index (prompt + guard agree on the assigned slot,
                  picked from the same pre-batch snapshot); LLM calls overlap, tool dispatch
                  serializes on one lock, and a nodes-style view caps the batch at its real
                  open_slots. Keeps completion + cross-fix stall; auto-pauses a finished component.
                services.py — Services, the BOUNDED gateway a Fix calls through (connector +
                  tool dispatch + pause/cancel checkpoint + per-fix step budget; BudgetExhausted
                  is a BaseException, so a fix can't churn past its cap). A count-driven target
                  needs no bespoke loop: `get_errors` fans a shortfall into one per-slot create-error
                  each (`checks.slot_errors`), so the outer loop authors one item per step. The
                  create step's write tool is wrapped by the slot guard (`_create_guard`, installed
                  from the count check's `guard`) — no overwrite, next slot in order. `Services.dispatch`
                  ENFORCES the step's tool scope (`self.allowed`, set by `run` from the prompt's
                  allowed tools = the SAME source as the offered schemas): a tool not scoped to this
                  step is refused, not dispatched — a small model learns other tool names from the
                  prompt prose and would otherwise thrash on an off-phase tool.
                  Scene AUTHORING is a turn loop (scenes.scene_turn_loop, via Check.run — a check
                  may own its whole multi-call fix body): one LLM call per character turn (system
                  prompt = that character's card + scene brief; the scene-so-far rendered as real
                  chat turns), with per-turn hygiene in code (dedupe kills two-agent circling and
                  seeds the lead-in against echoes; other-name prefixes rejected; third-person
                  NARR), then a closer call files the exit + story-state delta and the write goes
                  through write_scene — screenplay text (`NAME [emotion]: line`) parsed by
                  scenes.parse_screenplay into IR through write_node's validated path. Planned
                  ending nodes are forced to end; backward jumps/choices are rejected in code.
                  Turn calls run reasoning "none" (the connector maps it to chat_template_kwargs
                  enable_thinking:false — llama.cpp ignores reasoning.effort).
                rewrite.py — rewrite_node: regenerate ONE node from a human note (per-scene control)
                climb.py — module re-runner for prompt hill-climbing: clone a finished run, wipe ONE
                  module's component, drive the loop with only that module composed (same upstream
                  artifact + a candidate prompt = a comparable output).
                  `python -m maestro.climb <src_run_id> <module_id> [--label tag] [--keep]`
                run_control.py — cross-thread RunControl (pause/resume/cancel + auto_pause flag)
                  + per-run registry, the human-in-the-loop signal channel into the build thread
                tools.py — artifact tools (build_tools) + TOOL_SCHEMAS. write_component/
                  write_node/edit_node take a human-only `force` to override the done-lock
                  (the agent never sets it — not in TOOL_SCHEMAS); edit_node also takes full
                  `content` to replace a whole node (the manual per-scene editor)
                tools/spec_tools.py — propose_spec / amend_spec / freeze_spec (human gate); resolves
                  spec.params from each module's params() floors (int→max, list→union)
                story_state.py — continuity bible (facts, entities, threads, recent tail)
                run.py — create_run / run_build orchestrator + `python -m maestro.run` CLI
                tools/chat_tools.py — propose_game_spec / amend_game_spec (registered for chat)
                ir_assemble.py — lift the decomposed components → one engine-neutral IR dict
                ir_crossref.py — gate that every id reference in the IR resolves (crossref_records:
                  structured {path,ref,kind} → routing). Runs both as a cheap per-step `crossref`
                  done-condition (attributed) and inside the engine compile (backstop)
                engines.py — compile_for(spec.engine): map engine tag → backend compile entry
                prompts/ — climbable .txt prompts (spec_write.txt)
  renpy/        Ren'Py engine backend (one of N) the genre-agnostic maestro core wires in.
                The agent emits the engine-agnostic Game IR (docs/game_ir.schema.json) as JSON
                components; renpy/ projects the assembled IR to Ren'Py:
                compiler.py (compile_renpy — the spine, delegates to ir_compiler),
                ir_compiler.py (assemble → crossref gate → ir_vn/ir_pnc → write project → lint),
                ir_vn.py / ir_pnc.py (IR → script.rpy for VN / point-and-click),
                lint.py (SDK lint runner + line→component attribution),
                component_schemas.py (re-exports the modules' validators + IR skeletons),
                fns.py (two-pass image gen —
                neutral sprite + img2img expression variants — + manifest/expression
                placeholder backfills; generate_voices — best-effort per-line TTS for VN, silent
                .wav placeholder backfill), renpy_builder.py, templating.py, renpy_templates/
  web/          Self-contained browser backend (second engine). Same assemble_ir + crossref
                pivot; compiler.py/ir_compiler.py write game.json (the IR) + a static, pre-tested
                runtime (runtime/index.html,engine.js,style.css) that interprets the IR live —
                no per-game codegen. "lint" gate = JSON-Schema + crossref. Output opens in any
                browser / drops on any static host. Covers VN + point-and-click (combat pending).
  godot/        Godot 4 backend (third engine). Same assemble_ir + crossref pivot;
                compiler.py/ir_compiler.py write game.json (the IR) + a static, pre-tested
                GDScript runtime (runtime/*.gd) that interprets the IR live — no per-game codegen.
                "lint" gate = JSON-Schema + crossref (web model). Native export is best-effort
                (needs the godot binary + templates; absence never fails the build). Its REASON to
                exist is combat: combat.gd plays turn_based encounters web/renpy stub out. The
                `combat` module authors the encounter IR ONE slice at a time in dependency order
                (set_combat_meta lays stats/statuses, then write_ability/write_combatant/
                write_encounter grow the list slices, each validated against the declared upstream
                ids at write time — so a small model fixes one item, never re-authors the whole
                block); since it registers a projection ONLY here (none for renpy/web), any spec that
                picks it auto-routes to Godot via Module.engine_for — no explicit routing.
                runtime/Game.gd drives a PRESENTERS
                registry keyed on place.kind: `room` => pnc.gd (click hotspots), `world_map/town/
                interior` => overworld.gd (a WASD/arrow-key walkable tile grid — step ONTO a
                move/start_combat cell to fire it, press E on a talk/examine/take/use/win cell). A
                walkable place is PAINTED as `tiles` (a char grid: `rows` of tile chars + a `legend`
                mapping each char to a role — open/blocked = the only thing play reads — plus a free
                `theme` string). The grid size is just the shape of rows (any size), passability is
                derived from role (no separate `impassable`), and the map renders from the tiles —
                no background image. Each distinct theme gets one generated terrain texture
                (renpy/fns.generate_images tile pass via comfyui build_tile_job(theme, role) —
                a dedicated square DreamShaperXL-Turbo workflow with texture-language prompts,
                role-aware so open ground and blocked obstacles read differently — then
                make_seamless_tile post: wrap cross-fade + downscale to 256² → tile_<slug>.png);
                the presenter samples each cell's REGION of the seamless texture (one texture
                spans a 3×3 cell block, wrapping exactly) so terrain flows across cells, shows
                the generated item icon on take/use hotspots, keeps labels visible only near the
                avatar, and colour-fills when art is absent.
                Game.run_action is the one shared verb dispatch both presenters call, so a new
                navigation modality = a new presenter + one registry entry, never a router edit.
                docs/examples/combat_game.json is the hand-authored showcase exercising the full
                combat spec + the walkable world (multi-zone move tiles, walls, potion picked up on
                the map then drunk IN a fight via an ability gated on `requires:{item}` that
                consumes it with a `combat_effect.world` remove_item).
  tools/        tool_manager.py, system_tools, comfyui_tools, file_tools, execution_context
```

**Inference path (chat agent)**: `MainAgent` → `MessageBuilder` → `get_connector()` → `OpenAICompatibleConnector`
**Inference path (build agent/spec drafting)**: `agent.make_llm_decider` / `PipelineAgent.send()` → `MessageBuilder` → `call_llm()` → connector

The connector speaks **only** the OpenAI-compatible Responses API (`/v1/responses`) — the chat/completions path was removed. It's the only LM Studio endpoint that honors `reasoning.effort` (the lever that caps a local reasoning model's thinking tokens). `OpenAICompatibleConnector` translates the chat-shaped messages/tools callers pass into Responses `input`/`tools` and normalizes the response (and the SSE stream) back to chat shape, so call sites are unchanged. JSON mode rides on `text.format`, not `response_format`.

**Adding an artifact capability**: add a tool to `maestro/tools.py` (`build_tools` + `TOOL_SCHEMAS`). The agent composes it; the owning module's `get_errors` proves it (and `get_correction_prompt` gates the tool to the fix).

**Engines**: the IR is the pivot; a backend is a target it projects to. `spec["engine"]` (default `"renpy"`, also `"web"`/`"godot"`) selects it; `maestro.engines.compile_for` maps the tag to a `compile_*(working_dir, distribute=bool) -> Dict` entry returning a uniform pass/fail. Both the in-loop compile tool and the final packaging dispatch through it, so the loop is engine-agnostic. `assemble_ir` + `ir_crossref` (maestro core) are the shared, engine-neutral seam; the modules' structural checks (`maestro/modules/checks.py`) are also engine-neutral (they walk the IR graph). Adding an engine = a new `compile_*` in its own package (project the assembled IR to that engine's format) + one entry in `engines.py` — never branch the core.

**The Game IR**: the agent writes JSON, never engine source — `docs/game_ir.schema.json` is the engine-agnostic contract (nodes/places/actions/conditions/effects/combat); `docs/game_ir_decisions.md` is the rationale. The components are decomposed on disk (characters + asset_manifest + `nodes` [+ `places`/`items`/`story`]); at compile, `maestro.ir_assemble.assemble_ir` lifts them into one IR dict, `maestro.ir_crossref` gates that every id reference resolves — both as a cheap per-step `crossref` done-condition emitted by whichever realization module (`scenes`/`world`) owns the IR entry, and again inside the engine compile as a backstop — then the selected engine projects it (Ren'Py: `ir_vn`/`ir_pnc` → `script.rpy`; web: `game.json` + static runtime). This removes whole error classes (quote escaping, speaker format, menu indentation, dangling jumps) by construction and makes validation a data walk, not regex over engine source. A dialogue line carries an optional `emotion` (neutral/happy/sad/angry/surprised/worried); `ir_assemble` derives a per-character `expressions` map (only the emotions actually spoken), the asset pipeline img2img's each variant off the neutral base, and `ir_vn` swaps the speaker's sprite per line so faces change as they talk.

**Substrates + mechanic-modules** (`docs/ir_architecture.md`): a game = one **substrate** (execution model — `discrete_state` today; `real_time_sim` designed, not built) + a composed set of **mechanic-modules** the spec selects. A `Module` (`maestro/modules/module.py`) is **behavior, not a data bag**: the single ABC every module subclasses directly (there are NO intermediate base classes), and a module IS its `checks` list — a list of `Check`s, each pairing a `detect(check, module, context) -> [Error]` with how the loop FIXES what it emits (the `prompt`/`tools`/`skeleton`/`guard`/`max_tokens` for the correction step, or a `build_prompt` escape hatch that assembles the whole CorrectionPrompt — state/human). The base runs the list: `get_errors` sweeps the checks in declared order (a `blocking` check that emits stops the sweep and suppresses everything below it — a hard dependency tier; a `when_clean` check is skipped once anything is emitted — a terminal check like crossref/compile), and `get_correction_prompt`/`get_fix` find the emitting error's check by `Error.code` and build its fix. No module overrides these methods. `get_correction_prompt` + `render_context` fall back to module-default attrs (`mode_prompt`/`mode_tools`/`skeleton`/`projector`) when a check leaves a field None. The core invariant is **detector = fixer**: a check reports an error only if it also declares the fix — a module is just a list of `(detect → fix)` over the shared components, with no "ownership" and no second-class status. `state` is the always-on wiring invariant — it authors nothing but enforces that every declared flag/variable/item has a producer AND a consumer (use-it-or-cut-it), detecting and fixing by editing the host node/place/items; "winning" is self-evident per mechanic, not a module. **The loop authors one item per step, always.** There is ONE fix shape: `get_fix(error) -> Fix` returns a single correction step. A count-driven target isn't a special loop — its check's `detect` fans the shortfall into one per-slot create-error each (`checks.slot_errors`; distinct `path` per slot → stable identity, so completing one shrinks the set = visible progress, no false stall), and the outer loop drives them one at a time. A create step's write tool is slot-guarded (`_create_guard`, installed by `_single_fix` from the check's `guard`: no overwrite, next slot in order). The Fix runs through a bounded `Services` (per-fix step budget + checkpoint; a fix physically cannot churn past its cap; `Services.dispatch` also enforces the step's tool scope), so the module owns the SHAPE of the fix while the loop+Services own the LIMITS. `scenes` is ONE class with no spine/npc boolean — its bar is read from the artifact (the narrative floor fires only when a `story` component is present). `compose(module_ids)` resolves ids → live module instances (the always-on `human`/`assets`/`state` are force-included). Sizing is data: each `Module.params()` declares its knob FLOORS (int→max, list→union when composed); `spec_tools` resolves them into `spec.params` (the proposer may raise, never lower) and `get_errors` reads the value — so "this game needs ≥4 characters" is durable spec data while the check that enforces it is code in the owning module. The spec drafter is one `spec_write.txt` that authors the story (title + `concept` hook + request paragraph + story_state_schema + optional sizing) AND picks the mechanic-modules from the catalog; there is no per-component done-condition list. Engine **projections** register separately (`renpy/projections.py`, `web/projections.py`) keyed `(engine, module_id)`; a `projected` module with no projection for the chosen engine makes the compile **fail fast** (`unprojectable`), never silently drop content.

**Module selection** (no genre/preset box): the proposer is shown the **selectable module catalog** (`Module.selectable_catalog()` — every module's `id` + `description`, minus the always-on foundation) inside `spec_write.txt` and picks the modules directly. To stop reflexive over-picking, the picks are a **`{id: reason}` map** — the proposer must justify each module in one sentence against the story it just wrote (a module it can't tie to the story is left out); `spec_tools` keeps these as `spec["module_reasons"]` (chosen modules get the proposer's reason, auto-pulled foundation/deps get an auto note) so the human sees WHY each module is in the set at the freeze gate. `tools/spec_tools.py` then calls `Module.resolve_modules(picks)`, which (1) force-includes the always-on foundation (`selectable=False` → `human`, `assets`, `state`), (2) expands each pick's `requires` transitively (e.g. `card_play`→`world`, `scenes`→`cast`), (3) validates the set has a realization module (`scenes`/`world`) and is projectable — falling back to a visual-novel bundle if not (there is no exclusion: `world`+`scenes` compose), and (4) derives `spec["engine"]` via `Module.engine_for` = the first `maestro.engines.ENGINE_TAGS` engine that can project the whole set (so `card_play`→`web`, `combat`→`godot`, everything else→`renpy`). Engine selection needs the projection registry populated at propose time, so `engines.ensure_projections_registered()` eagerly pulls in every backend's `register()`. `substrate` is `"discrete"` (the only one built). `run.run_build`, params, skeletons, and tool-scoping all key off the composed modules; the maestro core never branches on a genre string.

**Adding a mechanic-module**: subclass `Module` in `maestro/modules/<name>.py` — declare a `checks` list (one `Check` per error kind: its `detect` plus the `prompt`/`tools`/`skeleton`/`guard` its fix runs with; `job="fix"` for a correction vs the default `author`; `blocking`/`when_clean` for ordering) and set the authoring-default attrs (`component`/`mode_prompt`/`mode_tools`/`skeleton`/`schemas`/`projector`). No method is overridden — the base sweeps `checks`. For a count-driven content module, the count check's `detect` fans the shortfall into per-slot create-errors (`checks.slot_errors`) and declares `guard` (its write tool + id keys). Declare `params()` floors for any sizing knobs, and set the catalog attrs so the proposer can pick it (`description` + `requires`; `selectable=False` for an always-on foundation module). Register it (`register_module` in `maestro/modules/__init__.py`), register a projection per engine that renders it (`renpy/projections.py` / `web/projections.py`), and (for a new shape) extend `ir_assemble`/`ir_crossref` + a schema fragment in `docs/game_ir.schema.json`. No loop edit is needed — the loop only calls `get_errors` + `get_fix`. `scenes`/`world`/`combat` are the worked examples of a slot-guarded count target; `state`/`human` show the `build_prompt` escape hatch.

**Settings**: `src/config/settings.json` (gitignored). Copy from `settings.example.json`.
**Model categories**: `large` / `medium` / `small` — controls `message_budget_chars`, `max_iterations`, `use_json_mode`. Use `small` for local models.

**Run backend**: `source venv/bin/activate && python run.py`
**Run frontend**: `cd frontend && npm run dev`
**Run a build (CLI)**: `cd src && python -m maestro.run "<request>"` (propose → freeze → build)
**Recompile a finished run (CLI)**: `cd src && python -c "from renpy.compiler import compile_renpy; print(compile_renpy('<run_dir>', distribute=True))"` (re-projects the on-disk JSON components → Ren'Py, lints, packages). Web target: swap `from web.compiler import compile_web` / `compile_web(...)` → writes `<run_dir>/game_output/` (open `index.html` over HTTP, e.g. `python -m http.server` in that dir — `fetch` is blocked over `file://`). Godot target: swap `from godot.compiler import compile_godot` / `compile_godot(...)` → writes `<run_dir>/godot_output/` (open `project.godot` in Godot 4 and run, or use the best-effort native export).
**Run tests**: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`

---

## Code standards

### No half measures, no backwards compatibility
When a full fix is available, take it — never the partial patch that leaves the root cause in place. If the real fix means changing the loop, change the loop; don't bolt a workaround onto the symptom. We are both the producer and the consumer of this code: there are no external callers, no published API, no old data to migrate. So never add backwards-compat shims, deprecation paths, version flags, or "keep the old way working too" code. Delete the old way and move the call sites. This is the opposite of timidity — surgical means *small and complete*, not *small and half-done*.

### Minimal and surgical
Edit only what the task requires. No cleanup, refactoring, or "while I'm here" changes unless asked. Three similar lines beats a premature abstraction. No feature flags, backwards-compat shims, or half-finished stubs.

### Tests
Write tests for every non-trivial change. Tests must be meaningful — test behaviour and contracts, not implementation details. Run tests before reporting a task complete. Fix failures before moving on. Integration tests in `tests/integration/` require live services — skip unless testing connectors.

### Code review
After implementing any non-trivial change, self-review the diff: check for security issues, unintended scope creep, missing tests, and regressions. Do this before declaring done.

### Documentation
Keep CLAUDE.md and ROADMAP.md in sync with reality. When shipping a feature: mark it done in ROADMAP.md, update the current state section if the architecture changed, and update CLAUDE.md if the "how it works" or architecture sections are now wrong. Do this in the same commit as the code.

### Comments
Default: none. Only when the WHY is non-obvious (hidden constraint, workaround, subtle invariant). Never comment WHAT the code does.

### Error handling
Only validate at system boundaries (user input, external APIs, tool results). Don't add defensive fallbacks for things that can't happen.

---

## Keeping prompts hill-climbable

Hill-climb *tooling* is currently removed (eval is trimmed to grading finished artifacts — `eval/cli.py score game`). But keep prompts swappable so it can return:

1. **One `.txt` file per LLM call.** Never inline prompt strings in Python (`_SOME_PROMPT = "..."`). Each call gets its own file under a `prompts/` dir (`maestro/prompts/`, `renpy/prompts/`).
2. **Load via `render_template`** (`renpy/templating.py`): `prompt = render_template(_PROMPTS_DIR / "my_prompt.txt", ctx)`.
3. **Load-bearing system prompts** belong in a `.txt` too, not a hardcoded `_SYSTEM` string, if quality depends on them.

---

## Small model strategy

Small models aren't dumb — they're easily distracted. They follow the most recent, most concrete instruction in the context window. The architecture already helps: the executor rebuilds a minimal context each step (spec + to-do + scratchpad + story state) and keeps the growing artifact out of the window. Within per-call prompts:

1. **Decompose over one-shot** — produce N items with N calls (one node per `write_node`), not all at once. Prevents truncation, keeps each call focused.
2. **Output skeleton before field descriptions** — show exact JSON structure first with inline comments, not a bullet list then a separate example. Model fills a skeleton rather than constructing from scratch (see `prompts/spec_write.txt`).
3. **Crafted context per step** — each module's `render_context` composes exactly the blocks its call needs (the dialogue author gets full character cards + locations + story + lead-in lines; the places author gets the item catalogue + scene index); never the full transcript, never another component dumped whole.
4. **Use `model_category: "small"`** in settings — tighter context budget, fewer iterations, JSON mode.
5. **Reasoning off by default, escalate on stall** — local reasoning models build initial content fine with `reasoning: "none"` (fast, no thinking tokens), but spiral when a target stops progressing and rarely recover on their own. So `generate_with_tools(..., reasoning=...)` takes a per-call override: the node sub-loop and the decider keep effort off until they stall (no clean tool call for a couple iterations), then flip it to `high` for the rest of that target. Set `reasoning: "none"` in settings as the floor; escalation rides on top. Note some local models (e.g. qwen3.6) only honor on/off — graded efforts (low/medium) collapse to the same budget.
