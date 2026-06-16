# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

---

## Current state

**Mid-rebuild (branch `agentic-rebuild`):** the fixed pipeline DAG was ripped out and replaced with an agentic loop + human-gated frozen spec. See `new_architecture.md` for the design and CLAUDE.md for the current layout.

**Agentic build system (`src/maestro/`)** — backend complete (Phases 0–6), validated by 181 unit tests + a real-SDK compile; live end-to-end run pending (needs the local LLM up).
- **Spec** (`spec.py`) — per-game contract: components with typed done-conditions, frozen flag, dependency order. Drafted fresh per game by the agent (`spec_tools.propose_spec`, climbable `prompts/propose_spec.txt`); no human-authored genre schema.
- **Human gate** — `freeze_spec` is the human's out-of-band approval (no freeze tool); `amend_spec` (reason mandatory) un-freezes to pause for re-approval. Build tools refuse until frozen.
- **Durable state** (`state.py`) — per-run dir `<working_dir>/runs/<run_id>/`: component JSONs (ids = compile filenames), structured scratchpad (replace-not-append), story state. Source of truth; the transcript is never memory.
- **validate** (`validate.py`) — closed typed check set (exists / count / distinct / each_has / refs_resolve / compiles) → failure list = the recomputed to-do. Empty vs frozen spec = done.
- **Executor** (`executor.py`) — non-LLM loop; rebuilds minimal context each step; completion decided by validate; architecture-triggered milestone check-ins.
- **Build agent** (`agent.py`) — stateless-per-step LLM decider, one tool call per step from rebuilt context.
- **Tools** (`tools.py`) — write_component / write_node (fused with story-state delta), read_component / read_story_state, generate_asset, validate, compile_renpy, update_scratchpad, request_review.
- **Story state** (`story_state.py`) — continuity bible (facts / entities / open threads / recent tail); snapshot not log; spine-tracked.
- **Run** (`run.py`) — `python -m maestro.run "<request>"` CLI: propose → freeze → build → project path.

**Ren'Py capabilities (`src/renpy/`)** — `compile_renpy` (the spine: artifact → launchable project + lint/distribute gate), `build` + image generation, script assembly. (The old per-stage text generators remain in `fns.py` but are no longer wired into the loop — the agent authors content.)

**Inference** — unified path `PipelineAgent` / `make_llm_decider` → `MessageBuilder` → `OpenAICompatibleConnector`. Structured JSON output with fallback; repetition detection; `safe_history_content()`; streaming with empty-result detection.

**Platform** — tool manager (decorator, auto schema inference), WebSocket event bus, model category settings (`large`/`medium`/`small`).

**Pending** — frontend rebuild (the old task UI was demolished; spec-review surface not yet built — `frontend/` still calls the removed `/api/tasks`); live-LLM end-to-end validation; eval is trimmed to grading finished artifacts (`eval/cli.py score game`), hill-climb tooling to return later.

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
