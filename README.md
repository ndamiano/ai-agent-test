# Maestro

An AI platform that makes things. The user says "make me a game" — an hour later, a good game exists. AI output quality is the product; everything else is scaffolding.

See `VISION.md` for the philosophy, `ROADMAP.md` for the plan and current state, and `CLAUDE.md` for architecture and code standards.

## How it works

You talk to Maestro through a chat interface. When you ask for something, Maestro does **not** write the artifact by hand and does **not** run a fixed pipeline. It drafts a per-game **spec** — a contract of components, each with checkable done-conditions — for you to review and freeze. Once frozen, a non-LLM **executor** drives an agentic loop that builds the artifact against the spec until every done-condition passes. "Done" means the artifact satisfies the frozen spec, decided by `validate` — never the agent claiming it.

The agent emits the engine-neutral **Game IR** (JSON — `docs/game_ir.schema.json`), never raw engine source. A selected backend projects the assembled IR to a runnable artifact.

**Genres** (the shape of the game):

| Genre | Produces |
|---|---|
| `vn` | A visual novel — dialogue graph (`nodes`) with choices, character sprites with per-line emotions, generated backgrounds |
| `point_and_click` | A room/hotspot adventure — clickable places, inventory, item-use puzzles, NPC dialogue, a win goal |

**Engines** (the target the IR projects to):

| Engine | Output |
|---|---|
| `renpy` | A packaged Ren'Py project (requires the Ren'Py SDK) |
| `web` | A self-contained static site — `game.json` + a pre-tested runtime; opens in any browser |

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

Settings live in `src/config/settings.json` (gitignored) and can also be edited from the web UI. Maestro talks to any OpenAI-compatible endpoint (LM Studio, etc.) via its Responses API; image generation uses ComfyUI. Set `model_category` to `small` when running local models. Building a Ren'Py game into a distributable requires the Ren'Py SDK (`renpy_sdk_path` setting or `RENPY_SDK` env var).

### Local model server

Maestro speaks the OpenAI-compatible `/v1/responses` API. Two known-good local servers; the `lmstudio` settings block is just that label — it points at either.

**LM Studio** — load a model, start its local server, point `lmstudio.base_url` at it (default `http://localhost:1234`).

**llama.cpp** (`llama-server`) — run in **router mode** so VRAM management can evict the LLM (see below). Router mode exposes the native `/models/load` + `/models/unload` endpoints; a single-model `llama-server -m model.gguf` pins its model in VRAM for the whole process lifetime and cannot be evicted.

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

The server flavor (LM Studio native REST vs llama.cpp router) is **auto-detected** from the endpoint — no flag to set.

### VRAM management

`comfyui.vram_management: true` makes image generation evict the LLM from VRAM first and reload it after, so one GPU time-shares between the language model and SDXL. It works with **both** LM Studio and a llama.cpp **router** (a single-model llama-server can't be evicted, so the LLM stays resident — leave this `false` and make sure the LLM + image model both fit at once).

### Voice (text-to-speech)

Optional per-line voice. Maestro POSTs each spoken line to a local OpenAI-compatible `/v1/audio/speech` server (e.g. **Kokoro-FastAPI**) and projects the clips into the Ren'Py build; a silent placeholder backfills any line that fails to synthesize.

```bash
# Kokoro-FastAPI, CPU image (tiny model — near real-time, zero GPU/VRAM contention).
# No --restart, so it never autostarts; manage it manually.
docker run -d -p 8880:8880 --name kokoro ghcr.io/remsky/kokoro-fastapi-cpu:latest
docker start kokoro   # when you want voice
docker stop  kokoro   # frees it
```

The GPU image's bundled PyTorch lacks Blackwell/sm_120 kernels, so use the CPU image on RTX 50-series. Settings:

```json
"tts": {
  "endpoint": "http://localhost:8880",
  "model": "kokoro",
  "voices": ["af_heart", "am_michael", "bf_emma", "bm_george"],
  "format": "wav"
}
```

`voices` are mapped onto cast members deterministically by id (empty → the server's default voice). Omit the whole `tts` block to disable voice. List the server's voices with `curl -s localhost:8880/v1/audio/voices`.

## Build a game from the CLI

```bash
cd src && python -m maestro.run "<request>"   # propose → freeze → build
```

## Tests

```bash
cd src && python -m pytest ../tests/ --ignore=../tests/integration -q
```

Integration tests in `tests/integration/` require live LLM services.

## Evals

`eval/` grades finished artifacts with an LLM judge against rubrics (`eval/cli.py score game`). Hill-climbing (judge-scored prompt mutation) was removed in the rebuild and is slated to return; prompts are kept as swappable `.txt` files so it can. See `eval/EVAL.md`.

## Repository layout

```
src/
  agents/       MainAgent (chat persona — drafts/amends specs) + agent configs
  api/          FastAPI routers + WebSocket event bus
  config/       settings schema/manager
  llm_clients/  connectors, message builder, shared inference primitives
  maestro/      the agentic build system — spec, state, validate, executor, tools,
                build agent, IR assemble/crossref, engine dispatch
  renpy/        Ren'Py engine backend (IR → script.rpy → packaged project)
  web/          Web engine backend (IR → game.json + static runtime)
  tools/        tool manager, ComfyUI, system tools, execution context
frontend/       chat-first React + Vite UI
eval/           rubrics, briefs, judge, scoring CLI
tests/          pytest suite
docs/           Game IR schema + rationale
```
