# Maestro

An AI platform that makes things. The goal is simple: user says "make me a game", an hour later a good game exists. The AI quality is the product — everything else (UI, install experience, visuals) is scaffolding that can be improved later.

The north star is magical output. A novel that rivals Dostoevsky. A game worth sharing. Not "technically completed" — actually good.

See `ROADMAP.md` for the full plan and current status.

---

## How it works

The user talks to Maestro via a chat interface. Maestro figures out what to make and makes it — either directly via tools, or by firing pipelines that are specialized for specific tasks. Maestro doesn't need to know how a pipeline works internally. It fires `run_pipeline("renpy")`, eventually gets back a completed game. Pipelines feel like tool calls.

**Pipelines are the key architectural idea.** They:
- Limit what's in the main agent's context (it sees inputs and outputs, not every intermediate step)
- Are specialized and optimized for their domain
- Can call other pipelines
- Can run in parallel (queue + gather pattern)
- Have strict dependency ordering when outputs feed into each other

---

## Architecture

```
src/
  agents/       MainAgent (agentic loop), MaestroAgent (wave orchestration), RefinerAgent
                chat.json / maestro.json / worker.json — agent configs + system prompts
  api/          FastAPI routers (tasks, chat, settings, agents, outputs, websocket)
  config/       settings_schema.py (Pydantic), settings_manager.py (singleton)
  database/     task_store.py (SQLite)
  llm_clients/  connector_selector.py, openai_compatible_connector.py, message_builder.py
                inference.py — PipelineAgent, call_llm (shared inference primitives)
  pipelines/    runner.py — DAG executor for LLM + Fn stages
                registry.py — pipeline registry
                renpy/ — 8-node pipeline (story→settings→characters→scenes→dialogue→package→images→build)
  tools/        tool_manager.py, orchestration_tools, system_tools, pipeline_tools
```

**Inference path (agents)**: `MainAgent` → `MessageBuilder` → `get_connector()` → `OpenAICompatibleConnector`  
**Inference path (pipelines)**: `PipelineAgent.send()` → `MessageBuilder` → `call_llm()` → connector (same path, unified)

**Adding a pipeline**: implement in `src/pipelines/<name>/`, register in `src/pipelines/registry.py`. No other changes needed.

**Settings**: `src/config/settings.json` (gitignored). Copy from `settings.example.json`.  
**Model categories**: `large` / `medium` / `small` — controls `message_budget_chars`, `max_iterations`, `max_waves`, `use_json_mode`. Use `small` for local models.

**Run backend**: `source venv/bin/activate && python run.py`  
**Run frontend**: `cd frontend && npm run dev`  
**Run tests**: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`

---

## Code standards

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

## Making pipelines hill-climbable

Hill climbing requires every LLM call prompt to be in a `.txt` file the eval system can swap. Follow this checklist when building or modifying a pipeline:

1. **One `.txt` file per LLM call.** Never inline prompt strings in Python (`_SOME_PROMPT = "..."`). Every call gets its own file under `pipelines/<name>/prompts/`.

2. **Wire `prompt_file` on every FnStage that makes an LLM call.** `prompt_file` tells the climb tool which file to swap. Set it in `pipeline.py`:
   ```python
   FnStage(id="my_stage", fn=my_fn, ..., prompt_file="my_prompt.txt")
   ```

3. **Load prompts via `render_template` in the function.** The file name in `prompt_file` and the file loaded in the function must match:
   ```python
   prompt = render_template(_PROMPTS_DIR / "my_prompt.txt", ctx)
   ```

4. **One LLM call per stage.** A FnStage that makes multiple LLM calls has only one `prompt_file` slot — only the registered file is climbable. If you need all calls independently tunable, split into separate nodes/stages. `story` currently violates this (arc, beat, locations in one stage) — tracked as a TODO.

5. **`LLMStage` is automatic.** Use `prompt_template=` instead of `prompt_file=` — the runner handles loading and swapping.

6. **System prompts.** Hardcoded `_SYSTEM` strings are not climbable. If a system prompt is load-bearing for quality, move it to a `system.txt` file and load it at call time.

---

## Small model strategy

Small models aren't dumb — they're easily distracted. They follow the most recent, most concrete instruction in the context window. The pipeline architecture already helps by keeping intermediate steps out of the agent's context. Within pipelines and per-call prompts:

1. **Decompose over one-shot** — any stage that produces N items should loop (one call per item) rather than generate everything at once. Prevents truncation, keeps each call focused. Applied to characters (3 calls: identity/appearance/voice) and scenes (1 call per beat).
2. **Output skeleton before field descriptions** — show exact JSON structure first with inline comments, not a bullet list then a separate example. Model fills a skeleton rather than constructing from scratch.
3. **Minimum injected context per stage** — each stage gets only what it actually needs. Characters in scene planning need role+personality, not appearance. Completed scenes for continuity need setting_id+character_ids, not full dramatic briefs.
4. **Use `model_category: "small"`** in settings — enables tighter context budget, fewer iterations, JSON mode.
