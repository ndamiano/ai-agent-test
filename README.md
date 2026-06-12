# Maestro

An AI platform that makes things. The user says "make me a game" — an hour later, a good game exists. AI output quality is the product; everything else is scaffolding.

See `VISION.md` for the philosophy, `ROADMAP.md` for the plan and current state, and `CLAUDE.md` for architecture and code standards.

## How it works

You talk to Maestro through a chat interface. Maestro figures out what to make and makes it — either directly via tools, or by firing **pipelines**: specialized, multi-stage generators that feel like tool calls from the agent's perspective. The agent sees inputs and outputs, never intermediate steps.

**Current pipelines:**

| Pipeline | Produces |
|---|---|
| `renpy` | A branching Ren'Py visual novel — procedural story DAG, multiple endings, generated art, packaged game |
| `character` | A richly detailed character — identity, personality, appearance, voice, portrait image |
| `ttrpg` | A complete TTRPG campaign document — world, factions, NPCs, encounters, quests |

## Setup

```bash
# Backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp src/config/settings.example.json src/config/settings.json   # then edit
python run.py

# Frontend
cd frontend
npm install
npm run dev
```

Settings live in `src/config/settings.json` (gitignored) and can also be edited from the web UI (gear icon). Maestro talks to any OpenAI-compatible endpoint (LM Studio, etc.); image generation uses ComfyUI. Set `model_category` to `small` when running local models. Building the visual novel into a distributable requires the Ren'Py SDK (`renpy_sdk_path` setting or `RENPY_SDK` env var).

## Tests

```bash
cd src && python -m pytest ../tests/ --ignore=../tests/integration -q   # backend
cd frontend && npx vitest run                                           # frontend
```

Integration tests in `tests/integration/` require live LLM services.

## Evals

`eval/` contains the hill-climbing system: capture pipeline outputs, score them with an LLM judge against rubrics, and mutate prompts to climb quality. See `eval/EVAL.md`.

## Repository layout

```
src/
  agents/       MainAgent (chat loop), MaestroAgent (wave orchestration), RefinerAgent
  api/          FastAPI routers + WebSocket event bus
  config/       settings schema/manager, agent configs
  database/     SQLite task store
  llm_clients/  connectors, message builder, shared inference primitives
  pipelines/    DAG runner, registry, and the pipelines themselves
  tools/        agent-facing tools (pipelines, files, ComfyUI, orchestration)
frontend/       React + Vite chat/tasks UI
eval/           rubrics, briefs, judge, hill-climbing CLI
tests/          pytest suite
```
