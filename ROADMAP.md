# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

---

## Current state

- Maestro agent (wave orchestration) + Worker agents (tool execution)
- Chat agent (conversational mode, persistent session, full tool access)
- Ren'Py pipeline (8-node DAG: story → settings → characters → scenes → dialogue → package → images → build)
- Pipeline registry + `run_pipeline` / `queue_pipeline` / `run_queued_pipelines` tools
- Agents can fire pipelines directly — the two systems are connected
- OpenAI-compatible connector (LMStudio, Cline, OpenRouter)
- Tool manager (decorator-based, auto schema inference)
- Task store (SQLite) + WebSocket event bus
- Model category settings (large/medium/small)
- React frontend: Chat tab + Tasks tab, branding as Maestro

---

## Architecture decisions

### Pipelines as tool calls
Pipelines are the key abstraction. They limit what's in the agent's context — it fires `run_pipeline("renpy_game")` and eventually gets back a completed game. It never sees intermediate steps. Pipelines are specialized, optimized, and composable.

### Pipeline execution model: queue + gather
- **`run_pipeline(name, brief)`** — fire a single pipeline, block until done, return result
- **`queue_pipeline(name, brief)`** — add a pipeline to the session queue (non-blocking)
- **`run_queued_pipelines()`** — run all queued pipelines in parallel, block until all complete

### Conversational mode
Persistent chat agent (MainAgent + chat.json config) with full tool + pipeline access. The "genie" interface. Chat history persists in localStorage.

---

## Phase 1 — Close the MVP gap ✓
- [x] Pipeline registry
- [x] `run_pipeline` / `queue_pipeline` / `run_queued_pipelines` tools
- [x] Conversational agent mode
- [ ] **Inference path unification** — pipeline stages bypass MessageBuilder + rate limiter; should go through same path as agents

## Phase 2 — Output quality
*Make it produce actually good output, especially on local/smaller models.*

- [ ] **Character field-group decomposition** — 3 sequential calls per character (identity / appearance / voice) instead of 1 monolithic call
- [ ] **Prompt tightening across all pipeline stages** — output skeleton shown before field descriptions; injected JSON trimmed to minimum needed per stage
- [ ] **Pipeline retry with smarter re-prompting** — on JSON failure, retry with stricter corrective prompt rather than identical prompt
- [ ] **Progress events from pipeline stages** — WebSocket updates so user knows what's happening mid-pipeline

## Phase 3 — More pipelines
*Coverage first. Every new pipeline makes Maestro feel more like a genie.*

Priority order driven by impact and "magical moment" potential:

- [ ] **Short story / creative writing pipeline** — prose output, chapter structure, character voices
- [ ] **Music generation pipeline** — integrate with a music model (e.g. MusicGen, Suno API)
- [ ] **Standalone character portrait pipeline** — character brief → multiple images (expressions, outfits)
- [ ] **RPG asset pipeline** — tilesets, sprites, item icons for RPG Maker or similar
- [ ] **More pipelines** — driven by user demand

Each new pipeline should be registerable in `pipelines/registry.py` with no other changes required.

## Phase 4 — Composition and scale
*Make pipelines composable and the platform more powerful.*

- [ ] **Pipelines calling pipelines** — formalize `run_pipeline` as callable from within FnStages
- [ ] **Parallel pipeline execution** — queue/gather pattern available within pipelines too
- [ ] **Pipeline parameter schema** — agents know what inputs each pipeline expects before firing
- [ ] **Automated prompt optimization** — evaluation suite + LLM-as-judge scorer + hill-climbing loop to improve stage prompts. Worthwhile once there are 3+ pipelines and a body of outputs to evaluate against.

## Phase 5 — Accessibility
*Once quality and coverage are there, make it easy for everyone.*

- [ ] Auto-install and manage ComfyUI / LMStudio
- [ ] Model selection assistant (help user pick the right model)
- [ ] Plugin / contribution system for third-party pipelines and tools

---

## What doesn't need to change

- Tool manager decorator pattern + schema inference
- MessageBuilder context budgeting and deduplication
- Pipeline DAG runner (LLMStage + FnStage, parallel stages, retry)
- Connector abstraction (OpenAI-compatible, streaming, swappable)
- Task store + WebSocket event bus
- Maestro wave orchestration model
