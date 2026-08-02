# Maestro

An AI platform that makes things. The user says "make me a game" — a while later, a good game exists. AI output quality is the product; everything else is scaffolding.

See `docs/vision.md` for the philosophy, `docs/roadmap.md` for the plan and current state, and `CLAUDE.md` for architecture and code standards.

## How it works

Press **Make a new game**, describe the game you want, and press **Build**. What you typed is byte for byte the one message the model is given, so approving it and building it are the same act. Nothing is stored server-side until you build.

A non-LLM **driver** then hands the model six tools — `list_files`, `read_file`, `write_file`, `edit_file`, `generate_media`, `done` — plus a running transcript, and lets it write the game. The model decides the file layout, the systems, and what art gets drawn; it calls `done` when the game is playable. Output is **plain browser HTML/CSS/JavaScript**, served as written. A 3D game imports the vendored three.js copied into every game folder.

A build reaches `built` when `index.html` exists — a gate may only detect BROKEN, never "bad", so whether a game is any *good* stays a human judgement. Play it, then say what to change: `python -m maestro.codegen.run --fix <run_id> "<note>"`.

The game asks for its own art as it writes the code that uses it: `generate_media(id, prompt, kind)` enqueues one render and answers immediately with the path the file will appear at, so the GPU draws while the model keeps writing.

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

Settings live in `src/config/settings.json` (gitignored). Set `model_category` to `small` when running local models.

**Accounts are provisioned manually — there is no signup route.**

```bash
cd src
python -m auth.cli create <handle>      # prompts for a password
python -m auth.cli grant  <handle> <n>  # accounts start at 0 credits
```

### Workers (the only path to a GPU)

The queue is the only transport to a GPU. Every backend — LLM, images, meshes — is a worker agent that PULLS jobs over `/worker`, authed by the shared `workqueue.token`. **A queue with no worker running means every job on it times out**, so the `llm` worker is mandatory.

```bash
python -m worker.agent --server http://localhost:8000 --token <token> --queue llm   --target http://localhost:8080
python -m worker.agent --server http://localhost:8000 --token <token> --queue image --target http://localhost:8188  # ComfyUI
python -m worker.agent --server http://localhost:8000 --token <token> --queue mesh  --target http://localhost:8189  # TRELLIS
```

The control plane enqueues one canonical chat request; the worker translates it for whatever its target serves (`worker.agent --api chat|responses`).

### Local model server

`llama-server` in **router mode**, so the model id is a filename stem rather than baked into the launch flags:

```bash
llama-server \
  --models-dir /path/to/your/gguf/dir \
  --host 127.0.0.1 --port 8080 \
  -ngl 99 \
  -c 32768 \
  --jinja \
  --reasoning-budget 0 \
  --chat-template-kwargs '{"enable_thinking":false}'
```

`--chat-template-kwargs` is load-bearing: without it Qwen3.6 thinks in `content` until authoring turns truncate at the output cap before the tool call, and `--reasoning-budget 0` alone does not stop it.

The model **id** is the GGUF filename stem (e.g. `Qwen3.6-27B-UD-Q4_K_XL`). Set the block to:

```json
"llm": {
  "model": "Qwen3.6-27B-UD-Q4_K_XL",
  "reasoning": "none",
  "n_ctx": 32768
}
```

The same string is the alias an autoscaled pod serves under, whichever engine its card runs — a
ninfer pod rejects any request naming something else.

A GPU serves one backend. Running the LLM and ComfyUI on one card means both must fit resident at once.

## Build a game from the CLI

```bash
cd src && python -m maestro.codegen.run "<request>"   # the request is the prompt → build
```

Play a build by opening `runtime/games/<run_id>/index.html`. Serve a 3D one over http rather than `file://` — `<script type="module">` is CORS-blocked from a file origin.

## Tests

```bash
cd src && python -m pytest ../tests/ --ignore=../tests/integration -q
```

Integration tests in `tests/integration/` require live services.

## Repository layout

```
runtime/
  vendor/       vendored three.js + GLTFLoader, copied into every game folder at seed
  games/        staged games, served at /play
  decimate.mjs  node: a finished TRELLIS GLB decimated to game weight
src/
  api/          FastAPI routers + WebSocket event bus
  auth/         identity, sessions, credit ledger, admin CLI
  config/       settings schema/manager
  db/           games/builds/events/jobs/workers + the compute budget, queue client, reaper
  llm_clients/  connector, message builder, wire translation
  maestro/
    codegen/    the build path — build_chain (driver), build_steps (turn machine),
                build_state (cursor), tools, staging, assets, asset_chain,
                prompts/, run (CLI)
    services.py, state.py, tool_calls.py
  scaler/       the RunPod autoscaler
  tools/        tool manager, ComfyUI, TRELLIS, system tools, execution context
  worker/       the pull-side GPU worker agent
  worldgen/     standalone procedural world generator
frontend/       React + Vite UI
tests/          pytest suite
docs/           vision, roadmap, deploy
```
