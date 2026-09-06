# Maestro

An AI platform that makes things. The user says "make me a game" — a while later, a good game exists. AI output quality is the product; everything else is scaffolding.

`CLAUDE.md` is the doctrine and the code standards. The rest is under `docs/`:

| doc | for |
|---|---|
| `vision.md` | the destination and the non-goals |
| `roadmap.md` | where we stand against it |
| `build_path.md` | the build path, module by module |
| `architecture.md` | processes, state, trust boundaries |
| `local_dev.md` | settings, model servers, how to run anything |
| `deploy.md` | the prod runbook and attaching GPU capacity |
| `backups.md` | DB snapshots, run archives, the restore drill |
| `experiments.md` | what each change to the loop actually measured |
| `game_rubric.md` | the form the owner grades a game with |
| `compute_billing_plan.md` | credits, metering, unit economics |
| `technology_analysis.md` | what we run today and what is on watch |
| `catalogue_design.md` | the shared asset catalogue, design only |

## How it works

Press **Make a new game** and describe the game you want. A designer turns your words into a systems design, and the build starts on it the moment it lands. Nothing is stored server-side until you press Make.

A non-LLM **driver** then hands the model seven tools — `list_files`, `read_file`, `write_file`, `edit_file`, `generate_media`, `compose_world`, `done` — plus a running transcript, and lets it write the game. The model decides the file layout, the systems, and what art gets drawn; it calls `done` when the game is playable. Output is **plain browser HTML/CSS/JavaScript**, served as written. Every game folder is seeded with the vendored three.js, GLTFLoader, BufferGeometryUtils and `world.js` (the loader for a `compose_world` world), plus `lib/` — a helper library for input, audio, a scaling canvas and lights, each file's header comment its API.

A build reaches `built` when `index.html` exists and the error gate — the staged game opened headless — finds no uncaught exception. A gate may only detect BROKEN, never "bad", so whether a game is any *good* stays a human judgement. Play it, then say what to change: `python -m maestro.codegen.run --change <run_id> "<note>"`.

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

Settings live in `src/config/settings.json` (gitignored); every block is described in `docs/local_dev.md`.

Signup is open at `/login`. Accounts can also be made from the CLI:

```bash
cd src
python -m auth.cli create <handle> <email>   # prompts for a password
python -m auth.cli grant  <handle> <n>       # accounts start at 0 credits
```

### Workers (the only path to a GPU)

The queue is the only transport to a GPU. Every backend — LLM, images, meshes — is a worker agent that PULLS jobs over `/worker`, authed by the shared `workqueue.token`. **A queue with no worker running means every job on it times out**, so the `llm` worker is mandatory.

```bash
python -m worker.agent --server http://localhost:8000 --token <token> --queue llm   --target http://localhost:8090  # ninfer (local 5090)
SAFETY_MODEL_DIR=<safety model dir> python -m worker.agent --server http://localhost:8000 --token <token> --queue image --target http://localhost:8188  # ComfyUI
python -m worker.agent --server http://localhost:8000 --token <token> --queue mesh  --target http://localhost:8189  # TRELLIS
```

The control plane enqueues one canonical chat request; the worker translates it for whatever its target serves (`worker.agent --api chat|responses`).

### Local model server

On the local 5090 the LLM is Qwen3.8 27B served by ninfer; the launch line and its load-bearing
flags are in `docs/local_dev.md` "Model servers". ninfer's `--model-id` is what `llm.model` must
name:

```json
"llm": {
  "model": "qwen3.8_27b",
  "reasoning": "none",
  "n_ctx": 131072
}
```

Prod serves a different model on a different card — Qwen3.8 Flash-Next on an RTX PRO 6000, under
the name `pennyroyal` (`docs/deploy.md`) — and there too `llm.model` is the name the engine
answers to; a pod refuses to boot under any other.

A GPU serves one backend. Running the LLM and ComfyUI on one card means both must fit resident at once.

## Build a game from the CLI

```bash
cd src && python -m maestro.codegen.run "<request>"            # the request is the prompt → build
```

Play a build by opening `runtime/games/<run_id>/index.html`. Serve a 3D one over http rather than `file://` — `<script type="module">` is CORS-blocked from a file origin.

## Tests

```bash
cd src && python -m pytest ../tests/ -q
```

## Repository layout

```
runtime/
  vendor/       three.js + GLTFLoader + BufferGeometryUtils + world.js, and lib/ (input, audio,
                canvas, lights) — all copied into every game folder at seed
  games/        staged games, served at /play
  decimate.mjs  node: a finished TRELLIS GLB decimated to game weight
src/
  api/          FastAPI routers + WebSocket event bus
  auth/         identity, sessions, credit ledger, play grants, admin CLI
  config/       settings manager + example
  db/           games/builds/events/jobs/workers + the compute budget, queue client, reaper
  grading.py    where a filled-in game grade is stored
  llm_clients/  connector, message builder, wire translation
  maestro/
    codegen/    the build path — build_chain (driver), build_steps (turn machine),
                build_state (cursor), tools, staging, assets, asset_chain,
                asset_use, error_gate, artifact_screen, snapshots,
                archive, turn_log, prompts/, run (CLI)
    worldgen/   the 3D world behind compose_world: planning, terrain, objects, backends
    services.py, state.py, tool_calls.py
  scaler/       the RunPod autoscaler
  tools/        ComfyUI, quilting, S3, DB backup, mailer, safety, execution context
  worker/       the pull-side GPU worker agent
frontend/       React + Vite UI
tests/          pytest suite
docs/           see the table above
scripts/        deploy, provision, the pod entrypoints, local_gpu (one card, three queues), db.py
```
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
frontend/       React + Vite UI
tests/          pytest suite
docs/           vision, roadmap, deploy
```
