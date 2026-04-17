# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

---

## Current state

- Maestro agent (wave orchestration) + Worker agents (tool execution)
- Ren'Py pipeline (8-node DAG: story → settings → characters → scenes → dialogue → package → images → build)
- OpenAI-compatible connector (LMStudio, Cline, OpenRouter)
- Tool manager (decorator-based, auto schema inference)
- Task store (SQLite) + WebSocket event bus
- Model category settings (large/medium/small)
- Basic React frontend

**Critical gap**: agents and pipelines are completely separate systems. No agent can fire a pipeline. The two biggest subsystems don't talk to each other.

---

## Architecture decisions

### Pipelines as tool calls
Pipelines are the key abstraction. They limit what's in the agent's context — it fires `run_pipeline("renpy_game")` and eventually gets back a completed game. It never sees intermediate steps. Pipelines are specialized, optimized, and composable.

### Pipeline execution model: queue + gather
Three tools for pipeline execution:

- **`run_pipeline(name, brief)`** — fire a single pipeline, block until done, return result
- **`queue_pipeline(name, brief)`** — add a pipeline to the session queue (non-blocking)
- **`run_queued_pipelines()`** — run all queued pipelines in parallel, block until all complete, return all results

This gives the agent natural parallelism: queue independent work (characters, settings, music), then gather — without needing real async in the agent loop.

### Conversational mode
A persistent chat agent with full tool access including pipeline tools. The "genie" interface — user talks, agent decides what to make and makes it. Simpler than Maestro; just MainAgent with the right tools and a long-lived session.

---

## Phase 1 — Close the MVP gap
*Blocking. Nothing else matters until this works end-to-end.*

- [ ] **Pipeline registry** — named dict of available pipelines + what brief params they expect
- [ ] **`run_pipeline` / `queue_pipeline` / `run_queued_pipelines` tools** — bridge between agents and pipelines
- [ ] **Conversational agent mode** — persistent chat session with tool access; entry point for "man I'm bored" → game
- [ ] **Inference path unification** — pipeline stages go through MessageBuilder + rate limiter, same as agents

## Phase 2 — Output quality
*Make it produce actually good output, especially on local/smaller models.*

- [ ] **Character field-group decomposition** — 3 sequential calls per character (identity / appearance / voice) instead of 1 monolithic call
- [ ] **Prompt tightening across all pipeline stages** — output skeleton shown before field descriptions; injected JSON trimmed to minimum needed per stage
- [ ] **Pipeline retry with smarter re-prompting** — on JSON failure, retry with stricter corrective prompt rather than identical prompt
- [ ] **Progress events from pipeline stages** — WebSocket updates so user knows what's happening mid-pipeline

## Phase 3 — Composition and scale
*Make it easy to build more complex things.*

- [ ] **Pipelines calling pipelines** — formalize `run_pipeline` as callable from within FnStages
- [ ] **Parallel pipeline execution** — queue/gather pattern available within pipelines too
- [ ] **Pipeline parameter schema** — agents know what inputs each pipeline expects before firing
- [ ] **More pipelines** — music generation, creative writing, image workflows, etc.

## Phase 4 — Accessibility
*Once quality is there, make it easy for everyone.*

- [ ] Auto-install and manage ComfyUI / LMStudio
- [ ] Model selection assistant (help user pick the right model)
- [ ] Plugin / contribution system for third-party pipelines and tools

---

## What doesn't need to change

The following are solid and should not be refactored without a strong reason:

- Tool manager decorator pattern + schema inference
- MessageBuilder context budgeting and deduplication
- Pipeline DAG runner (LLMStage + FnStage, parallel stages, retry)
- Connector abstraction (OpenAI-compatible, streaming, swappable)
- Task store + WebSocket event bus
- Maestro wave orchestration model
