# Maestro

An AI platform that makes things. The goal is simple: user says "make me a game", an hour later a good game exists. The AI quality is the product — everything else (UI, install experience, visuals) is scaffolding that can be improved later.

The north star is magical output. A novel that rivals Dostoevsky. A game worth sharing. Not "technically completed" — actually good.

**MVP**: Maestro agent + Ren'Py pipeline + basic UI. See `ROADMAP.md` for the full plan.

---

## How it works

The user talks to Maestro. Maestro figures out what to make and makes it — either directly via tools, or by firing pipelines that are specialized for specific tasks. Maestro doesn't need to know how a pipeline works internally. It fires `run_pipeline("renpy_game")`, eventually gets back a completed game. Pipelines feel like tool calls.

**Pipelines are the key architectural idea.** They:
- Limit what's in the main agent's context (it sees inputs and outputs, not every intermediate step)
- Are specialized and optimized for their domain
- Can call other pipelines
- Can run in parallel when there are no dependencies between them (async — fire and eventually resolve)
- Have strict dependency ordering when outputs feed into each other

The agent itself is also effectively a pipeline — some things can be created upfront, some in parallel, some have strict dependencies.

---

## Architecture

```
src/
  agents/       MainAgent (agentic loop), MaestroAgent (wave orchestration), RefinerAgent
  api/          FastAPI routers, websocket event bus, request/response models
  config/       settings_schema.py (Pydantic), settings_manager.py (singleton), agents/*.json
  database/     task_store.py (SQLite)
  engine/       pipeline_runner.py — DAG executor for LLM + Fn stages
  llm_clients/  connector_selector.py, openai_compatible_connector.py, message_builder.py
  pipelines/    renpy/ — 8-node pipeline (story→settings→characters→scenes→dialogue→package→images→build)
  tools/        tool_manager.py (decorator registration), orchestration_tools, system_tools
```

**Inference path (agents)**: `MainAgent` → `MessageBuilder` → `get_connector()` → `OpenAICompatibleConnector`  
**Inference path (pipeline)**: `PipelineRunner._call_llm` → `call_llm()` → connector directly  
**Known gap**: pipeline bypasses MessageBuilder and rate limiter — unification is a tracked todo.

**Settings**: `src/config/settings.json` (gitignored). Copy from `settings.example.json`.  
**Model categories**: `large` / `medium` / `small` — controls `message_budget_chars`, `max_iterations`, `max_waves`, `use_json_mode`. Use `small` for local models.

**Run backend**: `source venv/bin/activate && python run.py`  
**Run frontend**: `cd frontend && npm run dev`  
**Run pipeline directly**: `python run_pipeline.py`  
**Run tests**: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`

---

## Code standards

### Minimal and surgical
Edit only what the task requires. No cleanup, refactoring, or "while I'm here" changes unless asked. Three similar lines beats a premature abstraction. No feature flags, backwards-compat shims, or half-finished stubs.

### Tests
Write tests for every non-trivial change. Tests must be meaningful — test behaviour and contracts, not implementation details. Run tests before reporting a task complete. Fix failures before moving on. Integration tests in `tests/integration/` require live services — skip unless testing connectors.

### Code review
After implementing any non-trivial change, self-review the diff: check for security issues, unintended scope creep, missing tests, and regressions. Do this before declaring done.

### Comments
Default: none. Only when the WHY is non-obvious (hidden constraint, workaround, subtle invariant). Never comment WHAT the code does.

### Error handling
Only validate at system boundaries (user input, external APIs, tool results). Don't add defensive fallbacks for things that can't happen.

---

## Small model strategy

Small models aren't dumb — they're easily distracted. They follow the most recent, most concrete instruction in the context window. The pipeline architecture already helps by keeping intermediate steps out of the agent's context. Within pipelines and per-call prompts:

1. **Sequential field groups over monolithic JSON** — split complex objects into 2-3 focused calls (e.g. character identity / appearance / voice), merge results. Each call = simpler schema, focused attention, higher reliability.
2. **Output skeleton before field descriptions** — show exact JSON structure first, then explain fields. Model fills a skeleton rather than constructing from scratch.
3. **Minimum injected context per stage** — trim `{story_beats|json}` etc. to only what the stage actually needs. Large blobs burn attention.
4. **Use `model_category: "small"`** in settings — enables tighter context budget, fewer iterations, JSON mode.

---

## Open todos

1. Unify pipeline inference path with agent inference path
2. Break character generation into sequential field-group calls
3. Tighten pipeline prompts (output skeleton + trim injected JSON)
