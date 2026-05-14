# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

---

## Current state

**Agents**
- Maestro agent (wave orchestration) + Worker agents (tool execution)
- Chat agent (conversational mode, persistent session, full tool access)
- Agents fire pipelines as tool calls — sees inputs and outputs, never intermediate steps

**Pipelines**
- Ren'Py visual novel (8-node DAG: story → settings → characters → scenes → dialogue → package → images → build)
  - Characters: one `run_subpipeline("character")` call per cast member; portrait reused if already generated
  - Scenes: 1 call per beat with full dramatic brief (character_states, dramatic_question, revelation, what_changes, setting_constraint)
  - Dialogue: chunked generation (4 lines/chunk), continuity from prior lines, per-attempt retry with fresh agent
- Character creation (concept → identity/appearance/voice → portrait image)
- TTRPG campaign (setting → factions → NPCs → encounters → plot_hooks → campaign document)
- Pipeline registry — add a pipeline in `pipelines/<name>/`, register in `registry.py`, done

**Inference**
- Unified path: `PipelineAgent` → `MessageBuilder` → `OpenAICompatibleConnector` (same path for agents and pipelines)
- Structured JSON schema output (`response_format: json_schema`); graceful fallback if endpoint doesn't support it
- `frequency_penalty` (default 0.5) and separate `pipeline_max_tokens` (default 4096) per connector
- Repetition detection — raises before looping output poisons history
- `safe_history_content()` — strips local model channel markup from history before replay
- Streaming with empty-result detection; marks connector to skip streaming on subsequent calls
- All pipeline stage schemas are full JSON Schema (`type` + `properties`)

**Observability**
- Pipeline progress WebSocket events at every node/stage lifecycle (started, completed, retrying, failed)
- `PipelineProgress` UI panel: live status dots, progress bars per pipeline run, inline retry errors
- Request/response logs with correlated `request_id` UUIDs

**Platform**
- Tool manager (decorator-based, auto schema inference)
- Task store (SQLite) + WebSocket event bus
- Model category settings (`large` / `medium` / `small`)
- React frontend: Chat tab + Tasks tab, pipeline progress panel

---

## Architecture decisions

### Pipelines as tool calls
Pipelines limit what's in the agent's context. The agent fires `run_pipeline("renpy")` and eventually gets back a completed game. It never sees intermediate steps. Pipelines are specialized, optimized, and composable.

### Pipeline execution model
- **`run_pipeline(name, brief)`** — fire a single pipeline, block until done, return JSON outputs
- **`run_subpipeline(parent_dir, name, brief)`** — call a pipeline from within a FnStage; child working dir isolated under parent; returns `{status, pipeline, working_dir, outputs}`
- **`queue_pipeline(name, brief)`** — add pipeline to session queue (non-blocking)
- **`run_queued_pipelines()`** — run all queued pipelines in parallel, block until all complete

### Small model strategy
Decompose over one-shot. Every stage that produces N items loops (one call per item). Each call gets only what it needs — no full character objects where `id+role+personality` suffice. Show output skeleton before field descriptions. These principles apply to prompts and to the call structure itself.

### Local model hardening
Small local models fail in specific, detectable ways: repetition loops, channel markup in responses, streaming that silently returns nothing. Detect and handle each at the connector/agent boundary rather than letting failures propagate into pipeline state.

---

## Next — Composition and scale

- [ ] **Parallel subpipelines** — `run_subpipeline` currently sequential; queue/gather pattern within FnStages
- [ ] **Pipeline parameter schema** — agents know what inputs each pipeline expects before firing
- [ ] **Automated prompt optimization** — evaluation suite + LLM-as-judge scorer + hill-climbing loop. Worthwhile once there's a body of outputs to score against.

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
- Pipeline DAG runner (LLMStage + FnStage, parallel stages, retry)
- Connector abstraction (OpenAI-compatible, streaming, swappable)
- Task store + WebSocket event bus
- Maestro wave orchestration model
