# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

---

## Current state

- Maestro agent (wave orchestration) + Worker agents (tool execution)
- Chat agent (conversational mode, persistent session, full tool access)
- Ren'Py pipeline (8-node DAG: story → settings → characters → scenes → dialogue → package → images → build)
  - Characters: generated via `run_subpipeline("character", ...)` — full character pipeline per cast member, portrait reused if already generated
  - Scenes: 1 call per beat — full dramatic brief per scene (character_states, dramatic_question, revelation, what_changes, setting_constraint)
  - Dialogue: chunked generation (4 lines/chunk) with continuity from previous lines, per-attempt retry with fresh agent
  - All prompts tightened: skeleton-first format, minimum injected context per stage
- Pipeline registry + `run_pipeline` / `run_subpipeline` / `queue_pipeline` / `run_queued_pipelines` tools
- `run_subpipeline` — pipelines can call other pipelines; child runs in isolated working dir, returns structured result
- Agents can fire pipelines directly — the two systems are connected
- Unified inference path: `PipelineAgent` → `MessageBuilder` → connector (same path as agents)
- OpenAI-compatible connector with streaming + structured JSON schema output (`response_format: json_schema`), graceful fallback if unsupported
- `frequency_penalty` (default 0.5) + separate `pipeline_max_tokens` (default 4096) per connector setting
- Repetition detection in `PipelineAgent` — raises on looping output before it poisons history
- `safe_history_content()` strips local model channel markup (`<|channel>`, `<|start_header_id|>`) from history
- All pipeline schemas upgraded to full JSON Schema (`type` + `properties`) for structured output
- Pipeline progress WebSocket events — every node/stage lifecycle emitted to frontend; `PipelineProgress` UI component shows live status, progress bars, retry errors
- Tool manager (decorator-based, auto schema inference)
- Task store (SQLite) + WebSocket event bus
- Model category settings (large/medium/small)
- React frontend: Chat tab + Tasks tab, pipeline progress panel, branding as Maestro

---

## Architecture decisions

### Pipelines as tool calls
Pipelines are the key abstraction. They limit what's in the agent's context — it fires `run_pipeline("renpy")` and eventually gets back a completed game. It never sees intermediate steps. Pipelines are specialized, optimized, and composable.

### Pipeline execution model: queue + gather + compose
- **`run_pipeline(name, brief)`** — fire a single pipeline, block until done, return result
- **`run_subpipeline(parent_dir, name, brief)`** — call a pipeline from within a pipeline stage; child working dir is isolated under parent, returns `{status, pipeline, working_dir, outputs}`
- **`queue_pipeline(name, brief)`** — add a pipeline to the session queue (non-blocking)
- **`run_queued_pipelines()`** — run all queued pipelines in parallel, block until all complete

### Conversational mode
Persistent chat agent (MainAgent + chat.json config) with full tool + pipeline access. The "genie" interface. Chat history persists in localStorage.

### Small model strategy
Decompose over one-shot. Every stage that produces a large output should loop (one call per item) rather than generate everything at once. Each call gets only what it needs — no full character objects where id+role+personality suffice.

---

## Phase 1 — Close the MVP gap ✓
- [x] Pipeline registry
- [x] `run_pipeline` / `queue_pipeline` / `run_queued_pipelines` tools
- [x] Conversational agent mode
- [x] Inference path unification — `PipelineAgent` in `llm_clients/inference.py`

## Phase 2 — Output quality ✓
- [x] Character field-group decomposition — 3 sequential calls (identity / appearance / voice)
- [x] Scene decomposition — 1 call per beat with full dramatic brief
- [x] Enriched scene briefs — character_states, dramatic_question, revelation, what_changes, setting_constraint flow into dialogue
- [x] Prompt tightening — skeleton-first format, minimum injected context per stage
- [x] Pipeline retry with smarter re-prompting — PipelineAgent multi-turn correction on JSON failure

## Phase 3 — More pipelines (standalone, no sub-pipeline support needed) ✓

- [x] **Character creation pipeline** — concept → identity/appearance/voice (3-call decomposition) → portrait image. Brief: `concept` (required), `tone`/`role`/`setting`/`notes` (optional).
- [x] **TTRPG campaign pipeline** — setting → factions → NPCs (1 call per NPC loop) → encounters → plot_hooks → campaign document (markdown). Brief: `title`/`genre`/`tone`/`premise` (required), `scale`/`npc_count`/`player_count`/`notes` (optional). Scale drives NPC and encounter counts.

Each new pipeline is registerable in `pipelines/registry.py` with no other changes required.

## Phase 4 — Composition and scale
*Make pipelines composable and the platform more powerful.*

- [x] **Pipelines calling pipelines** — `run_subpipeline()` in `registry.py`; Ren'Py character generation uses it. Child working dir isolated, structured result returned.
- [ ] **Parallel pipeline execution** — queue/gather pattern available within pipelines too (currently sequential)
- [ ] **Pipeline parameter schema** — agents know what inputs each pipeline expects before firing
- [ ] **Automated prompt optimization** — evaluation suite + LLM-as-judge scorer + hill-climbing loop to improve stage prompts. Worthwhile once there are 3+ pipelines and a body of outputs to evaluate against.

## Phase 5 — Pipelines requiring composition

These pipelines are architecturally dependent on sub-pipeline support from Phase 4.

- [ ] **World building pipeline** — world bible → factions → nations/cities → characters. Eventual target: explorable artifact (VR world, interactive map). Each layer is its own sub-pipeline.
- [ ] **Long-form fiction / novel pipeline** — outline → chapters → continuity tracking across generations.
- [ ] **Comic / manga pipeline** — story → scenes → panels → dialogue + images per panel.
- [ ] **Music generation pipeline** — integrate with a music model (MusicGen, Suno API).
- [ ] **VR world pipeline** — see "North Star" section below.

## Phase 6 — Accessibility
*Once quality and coverage are there, make it easy for everyone.*

- [ ] Auto-install and manage ComfyUI / LMStudio
- [ ] Model selection assistant (help user pick the right model)
- [ ] Plugin / contribution system for third-party pipelines and tools
- [x] Progress events from pipeline stages — WebSocket events at every node/stage lifecycle; `PipelineProgress` UI component with live status, progress bars, retry errors

---

## North Star — VR World Generation

*"I want to explore a world where magic is real in VR" → hours/days later, a playable world.*

Full decomposition of what this requires:

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
- Sub-pipeline support (Phase 4) — NPC generation, world layout, asset generation are each their own pipelines
- Text-to-3D model — current quality (TripoSR, Shap-E) is rough but improving fast; this is the main blocker
- Godot integration — similar to Ren'Py but far more complex; scenes, scripts, assets must be assembled programmatically
- VR headset build pipeline — OpenXR export via Godot CLI

**What's achievable today:** world bible, NPC dialogue, quest outlines, asset specification — all LLM stages.
**The blocker:** 3D asset quality. Text-to-3D models are improving fast; revisit when output is good enough to be usable.
**Engine target:** Godot (open source, scriptable, OpenXR support, closest to Ren'Py in terms of programmatic project generation).

---

## What doesn't need to change

- Tool manager decorator pattern + schema inference
- MessageBuilder context budgeting and deduplication
- Pipeline DAG runner (LLMStage + FnStage, parallel stages, retry)
- Connector abstraction (OpenAI-compatible, streaming, swappable)
- Task store + WebSocket event bus
- Maestro wave orchestration model
