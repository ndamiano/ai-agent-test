# Maestro

An AI platform that makes things. The goal is simple: user says "make me a game", an hour later a good game exists. The AI quality is the product — everything else (UI, install experience, visuals) is scaffolding that can be improved later.

The north star is magical output. A novel that rivals Dostoevsky. A game worth sharing. Not "technically completed" — actually good.

See `ROADMAP.md` for the full plan and current status.

---

## How it works

The user talks to Maestro via a chat interface. When asked to make something, Maestro does NOT write the artifact by hand and does NOT run a fixed pipeline. It drafts a per-game **spec** — a contract of components, each with checkable done-conditions — for the human to review and freeze. Once frozen, a non-LLM **executor** drives an agentic loop that builds the artifact against the spec until every done-condition passes.

**The agentic loop + frozen spec is the key architectural idea.** Three layers:
1. **Spec layer** (agentic, human-gated): the chat agent drafts a spec; the human reviews, edits, and freezes it. Build tools refuse until frozen.
2. **Executor** (NOT an LLM): drives the loop. Each step rebuilds a *minimal* context from durable on-disk state (spec + validate's to-do + scratchpad + story state + last result) — the transcript is never used as memory, so context stays ~constant as the game grows. Completion is decided by `validate`, never by the agent claiming done.
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
                spec.py — Spec (components, frozen flag, dep_order)
                state.py — RunState: durable per-run dir <working_dir>/runs/<run_id>/
                validate.py — typed done-condition checks → failure list (the to-do)
                executor.py — the non-LLM loop (stateless per step, completion via validate)
                tools.py — artifact tools (build_tools) + TOOL_SCHEMAS
                agent.py — the build agent (LLM decider: one tool call per step)
                spec_tools.py — propose_spec / amend_spec / freeze_spec (human gate)
                story_state.py — continuity bible (facts, entities, threads, recent tail)
                run.py — create_run / run_build orchestrator + `python -m maestro.run` CLI
                chat_tools.py — propose_game_spec / amend_game_spec (registered for chat)
                ir_assemble.py — lift the decomposed components → one engine-neutral IR dict
                ir_crossref.py — gate that every id reference in the IR resolves
                engines.py — compile_for(spec.engine): map engine tag → backend compile entry
                prompts/ — climbable .txt prompts (propose_spec.txt)
  renpy/        Ren'Py engine backend (one of N) the genre-agnostic maestro core wires in.
                The agent emits the engine-agnostic Game IR (docs/game_ir.schema.json) as JSON
                components; renpy/ projects the assembled IR to Ren'Py:
                compiler.py (compile_renpy — the spine, delegates to ir_compiler),
                ir_compiler.py (assemble → crossref gate → ir_vn/ir_pnc → write project → lint),
                ir_vn.py / ir_pnc.py (IR → script.rpy for VN / point-and-click),
                ir_checks.py (structured done-condition checks + node_view/place_view projectors),
                lint.py (SDK lint runner + line→component attribution),
                component_schemas.py (per-component structural validators + IR skeletons),
                spec_baseline.py (per-genre baseline floor), fns.py (image gen + manifest
                backfills), renpy_builder.py, templating.py, renpy_templates/
  web/          Self-contained browser backend (second engine). Same assemble_ir + crossref
                pivot; compiler.py/ir_compiler.py write game.json (the IR) + a static, pre-tested
                runtime (runtime/index.html,engine.js,style.css) that interprets the IR live —
                no per-game codegen. "lint" gate = JSON-Schema + crossref. Output opens in any
                browser / drops on any static host. Covers VN + point-and-click (combat pending).
  tools/        tool_manager.py, system_tools, comfyui_tools, file_tools, execution_context
```

**Inference path (chat agent)**: `MainAgent` → `MessageBuilder` → `get_connector()` → `OpenAICompatibleConnector`
**Inference path (build agent/spec drafting)**: `agent.make_llm_decider` / `PipelineAgent.send()` → `MessageBuilder` → `call_llm()` → connector

The connector speaks **only** the OpenAI-compatible Responses API (`/v1/responses`) — the chat/completions path was removed. It's the only LM Studio endpoint that honors `reasoning.effort` (the lever that caps a local reasoning model's thinking tokens). `OpenAICompatibleConnector` translates the chat-shaped messages/tools callers pass into Responses `input`/`tools` and normalizes the response (and the SSE stream) back to chat shape, so call sites are unchanged. JSON mode rides on `text.format`, not `response_format`.

**Adding an artifact capability**: add a tool to `maestro/tools.py` (`build_tools` + `TOOL_SCHEMAS`). The agent composes it; declare the done-conditions that prove it in the spec.

**Engines**: the IR is the pivot; a backend is a target it projects to. `spec["engine"]` (default `"renpy"`, also `"web"`) selects it; `maestro.engines.compile_for` maps the tag to a `compile_*(working_dir, distribute=bool) -> Dict` entry returning a uniform pass/fail. Both the in-loop compile tool and the final packaging dispatch through it, so the loop is engine-agnostic. `assemble_ir` + `ir_crossref` (maestro core) are the shared, engine-neutral seam; genre dispatch and `ir_checks` are also engine-neutral (they walk the IR graph). Adding an engine = a new `compile_*` in its own package (project the assembled IR to that engine's format) + one entry in `engines.py` — never branch the core.

**The Game IR**: the agent writes JSON, never engine source — `docs/game_ir.schema.json` is the engine-agnostic contract (nodes/places/actions/conditions/effects/combat); `docs/game_ir_decisions.md` is the rationale. The components are decomposed on disk (premise + asset_manifest + `nodes` [+ `places`]); at compile, `maestro.ir_assemble.assemble_ir` lifts them into one IR dict, `maestro.ir_crossref` gates that every id reference resolves (a hard compile gate), then the selected engine projects it (Ren'Py: `ir_vn`/`ir_pnc` → `script.rpy`; web: `game.json` + static runtime). This removes whole error classes (quote escaping, speaker format, menu indentation, dangling jumps) by construction and makes validation a data walk, not regex over engine source.

**Genres**: two game shapes on the same agentic loop. `vn` (visual novel): premise + asset_manifest + `nodes` (dialogue graph). `point_and_click` (room/hotspot adventure): premise (NPCs) + asset_manifest (+item icons) + `nodes` (NPC dialogue the talk-actions `call`) + `places` (clickable screens with structured action verbs, inventory, item-use puzzles, win goal). The maestro core stays genre-agnostic — it dispatches sub-runners/projectors/baseline by component id. `spec_tools.propose_spec` auto-detects the genre from the request (keyword match, then a classifier call), tags `spec["genre"]`, and renders the matching `prompts/propose_spec[_pnc].txt`; `run.run_build` and `renpy/` key their build/checks/skeletons off that tag. Adding a genre = a new component shape (schema + skeleton + baseline + checks) and a compiler in `renpy/`, wired by genre in `run.run_build` — never branch the maestro core.

**Settings**: `src/config/settings.json` (gitignored). Copy from `settings.example.json`.
**Model categories**: `large` / `medium` / `small` — controls `message_budget_chars`, `max_iterations`, `use_json_mode`. Use `small` for local models.

**Run backend**: `source venv/bin/activate && python run.py`
**Run frontend**: `cd frontend && npm run dev`  *(frontend is mid-rebuild — see ROADMAP)*
**Run a build (CLI)**: `cd src && python -m maestro.run "<request>"` (propose → freeze → build)
**Recompile a finished run (CLI)**: `cd src && python -c "from renpy.compiler import compile_renpy; print(compile_renpy('<run_dir>', distribute=True))"` (re-projects the on-disk JSON components → Ren'Py, lints, packages). Web target: swap `from web.compiler import compile_web` / `compile_web(...)` → writes `<run_dir>/game_output/` (open `index.html` over HTTP, e.g. `python -m http.server` in that dir — `fetch` is blocked over `file://`).
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

## Keeping prompts hill-climbable

Hill-climb *tooling* is currently removed (eval is trimmed to grading finished artifacts — `eval/cli.py score game`). But keep prompts swappable so it can return:

1. **One `.txt` file per LLM call.** Never inline prompt strings in Python (`_SOME_PROMPT = "..."`). Each call gets its own file under a `prompts/` dir (`maestro/prompts/`, `renpy/prompts/`).
2. **Load via `render_template`** (`renpy/templating.py`): `prompt = render_template(_PROMPTS_DIR / "my_prompt.txt", ctx)`.
3. **Load-bearing system prompts** belong in a `.txt` too, not a hardcoded `_SYSTEM` string, if quality depends on them.

---

## Small model strategy

Small models aren't dumb — they're easily distracted. They follow the most recent, most concrete instruction in the context window. The architecture already helps: the executor rebuilds a minimal context each step (spec + to-do + scratchpad + story state) and keeps the growing artifact out of the window. Within per-call prompts:

1. **Decompose over one-shot** — produce N items with N calls (one node per `write_node`), not all at once. Prevents truncation, keeps each call focused.
2. **Output skeleton before field descriptions** — show exact JSON structure first with inline comments, not a bullet list then a separate example. Model fills a skeleton rather than constructing from scratch (see `prompts/propose_spec.txt`).
3. **Minimum context per step** — the executor injects only the to-do, scratchpad, and story-state snapshot; never the full transcript or prior script.
4. **Use `model_category: "small"`** in settings — tighter context budget, fewer iterations, JSON mode.
5. **Reasoning off by default, escalate on stall** — local reasoning models build initial content fine with `reasoning: "none"` (fast, no thinking tokens), but spiral when a target stops progressing and rarely recover on their own. So `generate_with_tools(..., reasoning=...)` takes a per-call override: the node sub-loop and the decider keep effort off until they stall (no clean tool call for a couple iterations), then flip it to `high` for the rest of that target. Set `reasoning: "none"` in settings as the floor; escalation rides on top. Note some local models (e.g. qwen3.6) only honor on/off — graded efforts (low/medium) collapse to the same budget.
