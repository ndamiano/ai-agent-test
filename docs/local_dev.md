# Running Maestro locally

Everything needed to bring the platform up on one box, and the settings that decide how it behaves.
`docs/deploy.md` is the same story for prod.

---

## Bring it up

Four things run: the control plane, the frontend, whatever serves the models, and one worker per
queue. The control plane touches no GPU at all — the worker-pull queue is the only transport — so a
queue with no worker means every job on it times out.

**Backend** — `source venv/bin/activate && python run.py`

**Frontend** — `cd frontend && npm run dev`

**Workers** (own terminal each, from `src/`, after `source venv/bin/activate`). One worker per
queue, and a queue owns its card. Check the ports against what you actually launched:

```
python -m worker.agent --server http://localhost:8000 --token <workqueue.token> --queue llm   --target http://localhost:8090
SAFETY_MODEL_DIR=<safety model dir> python -m worker.agent --server http://localhost:8000 --token <workqueue.token> --queue image --target http://localhost:8188
python -m worker.agent --server http://localhost:8000 --token <workqueue.token> --queue mesh  --target http://localhost:8189
```

Defaults if omitted: server `localhost:8000`, target `localhost:1234`, queue `llm`. The token is
`workqueue.token` from settings.json.

**One card, three queues, `auto`** — a world build (`compose_world`, or `worldgen.build.build_world`
by hand) alternates llm/image/mesh jobs many times and
blocks until each completes, so there is no point at which the legs can be drained "one at a time
by hand". `scripts/local_gpu.py auto` runs the control plane's other half instead: it polls all
three queues, starts (model + worker) whichever has pending work — favoring the queue it already
holds so a momentary empty doesn't thrash it — and stops what it started on SIGINT/SIGTERM. Run it
in one terminal alongside `python run.py`; `--idle-exit SECONDS` stops and exits once nothing is
pending anywhere for that long (default: never). The single-leg (`llm`/`image`/`mesh`) and `all`
modes still exist for draining one queue by hand.

A build that calls `compose_world` needs `auto` for the same reason: the world's stages and the
build's own turns share the llm queue, and its art rides the other two while the build keeps
writing code.

**Tests** — `cd src && python -m pytest ../tests/ -q`, frontend
`cd frontend && npm test` (vitest).

---

## Model servers

### LLM — ninfer (preferred on a 5090)

Same weights in its own artifact, ~3x llama.cpp's decode rate on the same card (194.5 vs 63.3
tok/s), compiled for `sm_120a` alone. It serves `chat` only.

```
<ninfer>/build/apps/ninfer-serve \
  <model>.ninfer \
  --model-id qwen3.8_27b \
  --host 127.0.0.1 --port 8090 \
  --max-context 131072 \
  --spec mtp --draft-tokens 3 --lm-head-draft \
  --presence-penalty 0 \
  --cors \
  --vision
```

`--vision` is what lets a worldgen build show the model its own renders — the refine stages send
images, and a server launched without it refuses every image-bearing turn (`vision_disabled`).
Its fixed GPU allocations come out of the same card as the weights and KV, and they are the
margin: `scripts/local_gpu.py` serves exactly `<llm.model>.ninfer` from `NINFER_MODELS` because a
larger variant artifact of the same model left no room for them and died at launch.
`--presence-penalty 0` is load-bearing: ninfer's sampler defaults to Qwen3 thinking defaults,
penalty 1.0 among them, which degrades long structured output. Thinking itself stays ON — there
is no `--no-thinking` — because a thinking build is a league better than a non-thinking one
(`docs/experiments.md`, 2026-08-25), and ninfer has no thinking-budget flag: the build's per-turn
output cap (`build_steps.MAX_TOKENS`, 50K) is what bounds it. `--model-id` must match `llm.model`
in settings.json.

### LLM — llama.cpp

```
llama-server \
  --models-dir <model dir> \
  --host 0.0.0.0 --port 8080 \
  -ngl 99 \
  -c 131072 \
  -fa on \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --jinja \
  --reasoning-budget 0 \
  --chat-template-kwargs '{"enable_thinking":false}' \
  --spec-type draft-mtp \
  -np 4
```

`--chat-template-kwargs '{"enable_thinking":false}'` is load-bearing and `--reasoning-budget 0`
alone is a no-op: without it Qwen3.6 thinks in `content` and authoring turns truncate at the output
cap before the tool call. `-fa on` plus q8_0 KV keeps the cache from spilling out of VRAM into host
RAM.

Set `-c` and `llm.n_ctx` to the same number — see the settings note below for why both directions
hurt.

### Images — ComfyUI

Local: `comfy-start`. Over the network: run `main.py --listen 0.0.0.0` from the Comfy checkout.

### Meshes — TRELLIS

```
<trellis venv>/bin/python src/tools/trellis_server.py \
  --repo <trellis repo> --weights <trellis weights> --stage-dir ""
```

A one-GPU box holds ComfyUI or TRELLIS, not both at once — which is why an asset top-up resumes a
mesh from its `<id>.src.png` rather than restarting at the image leg.

Two launch flags decide whether the server fits in HOST RAM, and a desktop that runs out of it
loses the whole user session to the OOM killer, not just the build (measured twice, 2026-08-21:
a 36 GB python killed at the mesh stage, Chromium and the desktop with it):

- `--stage-dir ""` — by default the server copies its weights into `/dev/shm` (the pod trick
  against a network filesystem dropping pages). On a local disk that copy is 14 GB of tmpfs that
  stays resident for as long as the box is up. Empty means load from `--weights`.
- leave `--ptype` at its `512` default. The server warms `{ptype, "512"}`, so `1024_cascade`
  loads TWO pipelines and the process peaks near 36 GB of host RAM. `scripts/local_gpu.py`
  launches with both settings.

---

## Builds from the CLI

```
cd src && python -m maestro.codegen.run "<request>"     # prompt → build
```

- `--new "<request>"` stops with the prompt on disk so it can be edited first
- `--build <run_id>` builds that stored prompt
- `--staged "<request>"` plans stages first, then builds them in order
- `--fix <run_id> "<note>"` applies a playtest note
- `--assets <run_id>` re-renders the art the game asked for and never got
- `--history <run_id>` lists a run's snapshots; `--restore <run_id> <ref>` puts the game back to one
- `--evict <run_id>` / `--rehydrate <run_id>` move a run dir to and from S3
- `--archive-all` uploads every settled run that has no archive yet (the nightly sweep in
  `docs/backups.md`)

**Playing a build:** `runtime/games/<run_id>/index.html`. A game is plain browser files, but a 3D
one needs http, not `file://` — `<script type="module">` is CORS-blocked from a file origin.

---

## Frontend notes

The palette is CSS variables in `frontend/src/index.css`; `tailwind.config.js` only names them, so
a colour change hot-reloads. Editing the config itself (a new name, a font) needs the dev server
restarted — Node caches the ESM config, and a stale one drops every custom class silently.

---

## Settings

`src/config/settings.json`, gitignored. Copy from `settings.example.json`.

### `llm`

`model`, `n_ctx`, `max_tokens`, `reasoning`. There is no endpoint — inference rides the queue.

`n_ctx` is the INPUT budget: when a transcript approaches it, `build_steps.compact` drops the
oldest whole rounds and re-grounds the model on the file listing (`CLAUDE.md`). Nothing else
trims.

Set it to the server's `-c`. The local router never reports its window, so both errors are yours to
avoid, and only one of them announces itself:

- **Too large** — nothing trims until the prompt has already overflowed.
- **Too small** — silent. A build whose files no longer fit one read-everything round compacts every
  turn, forgets, re-reads, and grinds to the turn cap. Measured 2026-08-01: a visual novel at
  `-c 32768` spent 31 compactions and ~100 of 120 turns re-reading its own five files.

`max_tokens` is only the connector's default ceiling — the build path passes its own.

### The wire format is the worker's, not a setting

The control plane enqueues a CANONICAL chat request; the worker translates it for whatever its own
target serves (`worker.agent --api chat|responses`, default `chat`; see `llm_clients/wire.py`).
`responses` is the only local dialect that honors `reasoning.effort`; `chat` is the universal one.

On `chat` a canonical body passes through minus `reasoning` — so **thinking and sampling are launch
flags on the target server, not request fields** (see the invocations above). Adding an engine is a
branch in `worker/handlers.llm`, never a settings change.

### `workqueue`

`token` is the worker bearer secret. There is no `enabled` flag and no GPU endpoint on this side.

exec_seconds are debited to the owning game by the `game_id` on the job row, and a job with none is
neither metered nor gated. Build and asset enqueues pass it directly; the one blocking path
(`queue_client.run_job`, what the connector's `generate_with_tools` rides) reads it off the
`run_scope` contextvar instead — a call there outside a scope spends GPU nobody is charged for.

### `data_dir`

Env `MAESTRO_DATA_DIR`, default `<repo>/data`. Where platform.db + auth.db live — control-plane
state, deliberately not under `working_directory`.

### `play.origin` / `play.app_origin`

Set BOTH to serve games from their own registrable domain; empty means one origin. settings.json
is the only place these live — prod's values are in `docs/deploy.md` "Public domains".

Either way /play auth is the handoff flow: the SPA mints a single-use token
(`POST /api/games/<id>/play-session`), `/handoff` redeems it into a per-game path-scoped grant
cookie, and the served `index.html` gets the console reporter injected on the way out (the game
folder stays pristine). See `docs/deploy.md` "Public domains".

### `smtp`

host/port/username/password/from_address — outbound mail, `tools/mailer.py`. One caller: the
password-reset link, whose address comes from `play.app_origin`. Unconfigured, the forgot-password
endpoint still answers ok (it must not report who has an account) and logs that it could not send —
so a box without it has no account recovery.

### `s3`

endpoint/region/bucket/keys — the off-box run-dir archive. An unconfigured bucket logs and stands
aside.

### `runpod`

The autoscaler (`src/scaler/` + `docs/deploy.md`): `enabled`, `api_key`, `network_volume_id`,
`cp_url` (the pod-reachable control-plane URL), `tick_seconds`, `stale_worker_seconds`, and
per-queue `queues.<name>` scaling blocks (template_id, gpu_type_ids, allowed_cuda_versions,
fallback_drops_cuda_floor, max_workers, thresholds, cooldown, idle_exit_seconds, boot_deadline_seconds). The `queues` dict
replaces the default wholesale — carry complete blocks. settings.json is the only place the block
lives; `docs/deploy.md` describes what each knob does to a pod.

`gpu_type_ids` is PRIORITY-ORDERED: the scaler asks for the head alone and widens to the whole list
only when RunPod refuses that create, since the cards are not substitutes (ninfer serves only a
5090). Which card a pod GOT is the worker's to report, from the device — a control plane that
records its own request records the first list entry forever (measured 2026-08-01: 879 prod jobs
stamped 5090, the bill entirely RTX PRO 4500).

The autoscaled llm image carries both engines and picks by reading the card at boot. `llm.model`
must be what the engine answers to: it reaches the pod as `LLM_MODEL` and becomes ninfer's
`--model-id`.

### `payments`

`stripe_secret_key`, `stripe_webhook_secret` — the storefront (`api/routers/billing.py`). Empty
means no purchases; credits still arrive by `auth.cli grant`.

### `demo_games`

`showcase` and `oneshot`, each a list of run ids the landing page serves unauthenticated
(`api/routers/demos.py`). Curation is a deploy-time decision: nothing a build does can put a game
here. Both empty means no demo surface.

---

## Environment variables

Everything structured is in settings.json; the env carries only what `.env.example` lists (server
bind, `WORKING_DIRECTORY`, `WORKQUEUE_TOKEN` + its timeouts, the `LLM_*` defaults) plus:

- `MAESTRO_DATA_DIR` — above.
- `MAESTRO_DEV=1` — uvicorn reload, `/docs` + `/redoc` served.
- `MAESTRO_STALE_PENDING_SECONDS` (default 1800) — `db/reaper.py`: a job pending this long with
  nobody claiming it is failed rather than left to hold its budget reservation forever.
- `MAESTRO_LOCAL_LOGS` (default `/tmp/maestro-local`) — where `scripts/local_gpu.py` writes the
  model-server and worker logs it starts.

---

## Reading the platform DB

`scripts/db.py` — stdlib only, runs anywhere the file is: named queries (`cost`, `builds`,
`models`, `failures`) and `sql "<text>"` for the rest; `--db` points it at another file, `--since`
at a date. Read it from a snapshot, never the live file (`docs/backups.md` "Query hygiene").

---

## The pre-commit hook

`git config core.hooksPath .githooks` once per clone. `.githooks/pre-commit` lists every comment
line the commit adds and asks whether each belongs (the standard is `CLAUDE.md` "Comments"). On a
terminal it prompts y/N; with no terminal it blocks and prints the list, and the same commit goes
through with `COMMENTS_REVIEWED=1` after the list has been read.
