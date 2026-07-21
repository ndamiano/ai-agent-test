# Maestro

An AI platform that makes things. The user says "make me a game" — an hour later, a good game exists. AI output quality is the product; everything else is scaffolding.

See `docs/VISION.md` for the philosophy, `docs/ROADMAP.md` for the plan and current state, and `CLAUDE.md` for architecture and code standards.

## How it works

You talk to Maestro through a chat interface. When you ask for something, Maestro does **not** write the game by hand and does **not** run a fixed pipeline. It drafts a per-game **spec** — title / genre / entities / controls / mechanics / win-lose — for you to review and freeze. Once frozen, a non-LLM **executor** drives the local model to author a folder of **TypeScript modules** against a primitive **kit**, patching until the local gates pass. "Done" means the artifact passes the gates (typecheck → headless → probe → render → scroll) — never the model claiming it.

The model writes **real TypeScript game code**, not an intermediate representation. Breadth comes from the model COMPOSING kit primitives (physics, collision, tilemaps, pathfinding, 3D) rather than from per-genre generators. `update(dt, input, kit)` mutates plain state and never draws; `draw(g, kit)` reads state and never mutates — so the sim runs headless in pure Node, and render (2D canvas or three.js) is the only engine-specific layer.

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

Settings live in `src/config/settings.json` (gitignored) and can also be edited from the web UI. Maestro talks to any OpenAI-compatible endpoint (LM Studio, etc.) via its Responses API; image generation uses ComfyUI and 3D meshes use a TRELLIS server. Set `model_category` to `small` when running local models.

### Local model server

Maestro speaks the OpenAI-compatible `/v1/responses` API. Two known-good local servers; the `lmstudio` settings block is just that label — it points at either.

**LM Studio** — load a model, start its local server, point `lmstudio.base_url` at it (default `http://localhost:1234`).

**llama.cpp** (`llama-server`) — run in **router mode**, so the model id is a filename stem rather than baked into the launch flags.

```bash
llama-server \
  --models-dir /path/to/your/gguf/dir \
  --host 127.0.0.1 --port 8080 \
  -ngl 99 \
  -c 32768 \
  --jinja \
  --reasoning-budget 0
```

The model **id** is the GGUF filename stem (e.g. `Qwen3.6-35B-A3B-UD-Q4_K_XL`). Set the block to:

```json
"lmstudio": {
  "base_url": "http://localhost:8080",
  "model": "Qwen3.6-35B-A3B-UD-Q4_K_XL",
  "reasoning": "none"
}
```

A GPU serves one backend. Running the LLM and ComfyUI on one card means both must fit resident at once.

## Build a game from the CLI

```bash
cd src && python -m maestro.codegen.run "<request>"   # draft → freeze → build
```

Play a build by opening `runtime/index.html?game=<slug>` in a browser (2D or 3D auto-routed).

## Tests

```bash
cd src && python -m pytest ../tests/ --ignore=../tests/integration -q
```

Integration tests in `tests/integration/` require live LLM services.

## Repository layout

```
runtime/        the primitive KIT (engine.js/engine3d.js), the game gates (headless/probe/
                render/scroll), ambient TS types (engine.d.ts), kit_api*.md, browser harness
src/
  agents/       MainAgent (chat persona — drafts/amends specs) + agent configs
  api/          FastAPI routers + WebSocket event bus
  config/       settings schema/manager
  llm_clients/  connectors, message builder, strip_fences helper
  maestro/
    codegen/    the build path — gates, tools (write/read/edit_game_file), module (the
                CodegenModule), fix_classes, prompts/, reskin (assets), run, worldgen_bridge
    agent_loop.py  the non-LLM executor that drives the module
    services.py    the bounded gateway a fix calls through
    modules/, state.py, run_control.py
  auth/         identity, sessions, credit ledger
  tools/        tool manager, ComfyUI, TRELLIS, system tools, execution context
frontend/       chat-first React + Vite UI
tests/          pytest suite
docs/           vision, roadmap, plan, deploy
```
