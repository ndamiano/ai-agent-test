# Deploy runbook

Deploying Maestro is deploying **one CPU-only container**: API + SPA (same-origin, single uvicorn
worker) + the job queue. Parameterized by `.env` — no code edits to deploy.

GPU capacity is attached separately and is not a deploy step; see [Attaching
capacity](#attaching-capacity).[^1]

[^1]: The control plane enqueues; workers claim over `/worker`. No GPU host appears in any
control-plane config, and prod never dials out. Contract details: `docs/architecture.md`.

---

## Files

- `Dockerfile` — multi-stage: node builds `frontend/dist`, a second node stage resolves the mesh
  toolchain, then a `python:3.12-slim` runtime installs deps and copies `run.py` + `src/` +
  `runtime/` + the built SPA, and runs as non-root.
- `docker-compose.yml` — the single `app` service (build, `.env`, the two named volumes, the
  `settings.json` bind mount, healthcheck).
- `.env.example` — every knob; copy to `.env` and edit.
- `scripts/provision.sh` — one-time host setup.
- `scripts/deploy.sh` — ship dev → prod.

The image build uses `Dockerfile.dockerignore`, which drops `src/worker/` +
`src/tools/trellis_server.py` — pod-side code that never runs on the control plane. `settings.json`
is dockerignored too and rides a host bind mount, so `runpod.queues` (which env can't express) is
editable without a rebuild — but the file must exist before the first `up`.

## The data invariant (critical)

Durable state lives on **two named Docker volumes**, both outside the rsync'd source tree:

- `maestro-data` → `/data`: `runs/` (via `WORKING_DIRECTORY=/data`) + `auth.db` (accounts, credit
  ledger) + `platform.db` (games, builds, jobs, events) (via `MAESTRO_DATA_DIR=/data`, set in
  docker-compose.yml).
- `maestro-games` → `/app/runtime/games`: staged playable bundles served at `/play`.

Both survive image rebuilds and `deploy.sh` runs. Never point `WORKING_DIRECTORY` or
`MAESTRO_DATA_DIR` off `/data`, and never `docker volume rm` either volume — that wipes accounts and
games.

## Node in the image

Games are plain HTML/CSS/JS: staging copies the folder straight to `/play`. Node is in the image for
one subprocess — `runtime/decimate.mjs`, which cuts a finished TRELLIS GLB down to game weight. It
needs `runtime/node_modules` (gltf-transform + meshoptimizer), npm-ci'd in a linux build stage so the
platform-specific binaries resolve.

---

## Brand-new box (provision → configure → deploy)

On the **prod box**:

```bash
# one-time host setup: Docker + compose (+ Node build toolchain for the container)
./scripts/provision.sh        # confirm the version vars at the top first
```

Two files must exist on the prod box before the first deploy — `deploy.sh` never ships either. In
the app dir (default `/opt/maestro`):

```bash
cp .env.example .env                  # then edit
touch src/config/settings.json        # the compose bind-mount target; fill in runpod.queues
```

Then from the **dev box**:

```bash
PROD_HOST=user@prod-box ./scripts/deploy.sh
```

`deploy.sh` rsyncs only what the box needs to build + run (source, runtime, frontend,
compose/Docker files, scripts — repo paperwork like docs/tasks/tests and the pod-side worker
Dockerfiles stay home), runs `docker compose build && docker compose up -d`, and curls prod
`/healthz` — failing loudly if any step errors. `rsync --delete` does not remove excluded paths, so
leftovers from older deploys need a one-time manual sweep.

**Redeploy:** the same `deploy.sh` command.

## Editing `.env` after the first boot

A plain `docker compose up -d` does **not** re-read a changed `.env` for an already-running container
— it keeps the env baked in at its last (re)creation, so your edit silently has no effect. Force it:

```bash
docker compose up -d --force-recreate
docker compose exec app printenv <VAR>     # confirm the value landed
```

(This bites `CP_URL` and `WORKQUEUE_TOKEN` in particular — a stale value leaves workers unable to
claim while `.env` on disk looks correct.)

## Accounts (no signup — manual only)

The CLI lives at `/app/src/auth/cli.py`; the container WORKDIR is `/app`, so run it from `/app/src`:

```bash
docker compose exec -w /app/src app python -m auth.cli create <handle>   # prompts for a password
docker compose exec -w /app/src app python -m auth.cli grant  <handle> <n>
```

Accounts start at 0 credits.

## Smoke test (do this before handing out the URL)

```bash
BASE=https://<prod-host>
curl -s -o /dev/null -w "%{http_code}\n" $BASE/healthz          # 200
curl -s -o /dev/null -w "%{http_code}\n" $BASE/                 # 200 (SPA)
curl -s -o /dev/null -w "%{http_code}\n" $BASE/api/games/       # 401 (gated)
```

Then in a browser: log in as a test account → request a game → confirm it builds → play it at
`/play/games/<run_id>/index.html`. The build needs an `llm` worker running or its first turn times
out.

---

# Attaching capacity

One worker process per queue, run wherever the GPU is. It dials out, so it needs no inbound port:

```bash
python -m worker.agent --server http://<control-plane>:8000 --token <WORKQUEUE_TOKEN> \
    --queue llm   --target http://localhost:8080     # llama.cpp router
python -m worker.agent ... --queue image --target http://localhost:8188   # ComfyUI
python -m worker.agent ... --queue mesh  --target http://localhost:8189   # TRELLIS
```

`--target` is the worker's own inference server and should stay bound to `127.0.0.1` — a reachable
one is an unauthenticated GPU. `scripts/maestro-worker.service` runs an agent under systemd on a box
you own.

A queue with no worker means every job on it times out.

## RunPod worker images

One image per queue, weights on a RunPod **network volume** (the image is code, the volume is
weights), so a pod boots without re-downloading 60 GB. Pods never talk to Hugging Face after
provisioning.

The volume also speaks the **S3 API**, which is how a single file reaches it without renting
anything — a full provision is 60 GB at datacenter bandwidth and wants a pod, but one added or
retired artifact is a `cp`/`rm` from the home box:

```bash
aws s3 ls --profile runpod --region eu-ro-1 \
    --endpoint-url https://s3api-eu-ro-1.runpod.io s3://<volume-id>/ --recursive --human-readable
aws s3 cp --profile runpod --region eu-ro-1 --endpoint-url https://s3api-eu-ro-1.runpod.io \
    /var/tmp/ninfer-models/qwen3_6_27b_nvfp4.ninfer s3://<volume-id>/models/ninfer/
```

The bucket name IS the network volume id, and the region/endpoint pair is the datacenter the volume
lives in — a volume in another datacenter answers on its own endpoint or not at all.

```bash
# 1. one-time: populate the volume. Any cheap pod with it mounted; no GPU used.
VOL=/workspace bash scripts/provision_volume.sh

# 2. build + push the three worker images (one Docker Hub repo, queue-version tags)
docker build -f Dockerfile.worker-llm   -t ndamiano100/maestro-worker:llm-v6 .
docker build -f Dockerfile.worker-image -t ndamiano100/maestro-worker:image-v5 .
docker build -f Dockerfile.worker-mesh  -t ndamiano100/maestro-worker:mesh-v13 .
docker push ndamiano100/maestro-worker:mesh-v13   # etc.
```

Deployed tags: `llm-v6`, `image-v5`, `mesh-v13`. Bump the tag on every push — RunPod caches images
per host, so re-pushing a tag leaves stale copies serving on warm hosts.

**The llm image carries BOTH engines and picks at boot.** ninfer serves the same 27B ~60% faster
but is compiled for `sm_120a`, so it runs on a 5090 and nowhere else; the entrypoint reads
`nvidia-smi --query-gpu=name` and starts ninfer on a 5090, llama.cpp on anything else. That is why
the volume holds the model twice (`models/ninfer/*.ninfer` and `models/LLM/*.gguf`, ~34 GiB
together) and why an llm pod needs `LLM_MODEL` in its env: ninfer refuses any request whose `model`
is not its `--model-id`, and the autoscaler delivers the control plane's `llm.model` at create.
The ninfer build stage compiles a pinned commit of github.com/Neroued/ninfer — it needs CUDA 13.1
(the base image ships 12.8 for llama.cpp; only `libcudart.so.13` is added).

**Deploy the control plane BEFORE pointing the llm template at `llm-v6`.** `LLM_MODEL` has no
default and the entrypoint refuses to start without one, so an `llm-v6` pod created by a control
plane that does not yet send it exits 1 at boot — and RunPod restarts an exited container and keeps
billing. Nothing recovers it until `boot_deadline_seconds` (900) expires, and the scaler creates a
replacement in the meantime. The reverse order is free: an older image ignores `LLM_MODEL` and only
stops reporting `GPU_TYPE`, which the worker now reads off the device anyway.

Make one RunPod **template** per image (container image + volume mount at `/workspace`; no ports).
Run each pod with the volume at `/workspace` and `CP_URL` + `WORKER_TOKEN` set (`WORKER_TOKEN` must
match `workqueue.token` on the control plane), plus `LLM_MODEL` on an llm pod. No pod exposes a
port: the inference server binds `127.0.0.1`.

**Mesh cold start (measured on a 5090 pod, 2026-07-23).** A pod reaches WARM — able to serve at
steady speed — in ~116s of the ~330s it used to take, and a claimed job never pays boot:

| phase | seconds | what removed the old cost |
|---|---|---|
| pod create → container running (13.7GB pull) | ~45 | image size — the remaining lever |
| stage 9.7GB volume → `/dev/shm` | ~5 | @2.7GB/s; the FUSE mount does NOT retain page cache, so prefaulting in place bought nothing and only loading from RAM holds |
| torch/trellis import | ~10 | |
| pipeline load | ~4 | skip default init (37s of a 43s load, all overwritten by the checkpoint) + load only the tier's 6 models, not all 8 |
| warmup mesh | ~35 | lazy encoders (DINOv3/BiRefNet) + first-use kernel compile, paid once at boot |
| every real job | ~13 | |

The entrypoint gates worker registration on `/health` reporting `"warm": true`. That costs no
wall-clock (nothing can generate earlier) and keeps `exec_seconds` honest — otherwise the first
claimed job is billed 53s for 13s of work. It also means a pod that cannot generate dies at boot
instead of failing a user's job. Staging needs ~11GB free in `/dev/shm`, so mesh pods want ≥32GB
RAM; below that the server logs `staging skipped` and loads off the volume (~48s instead of ~4s).

**Container restart caveat:** RunPod restarts an exited container and keeps billing — even exit 0.
A worker deciding to die is therefore not enough to stop the meter. Two layers handle it:
- **In-pod self-terminate** (fast path): clean agent exit → `DELETE /pods/$RUNPOD_POD_ID`, retried
  ×3, result logged. RunPod injects `RUNPOD_POD_ID` but **no API key** (verified live 2026-07-21),
  so this only fires if `RUNPOD_API_KEY` is in the pod env — which we deliberately do NOT pass to
  autoscaled pods (an account-wide key inside every pod is a bad trade). Without it the entrypoint
  logs "self-terminate skipped" and exits; expect the restart loop until the reaper acts.
- **The reaper** (the guarantee): the worker deregistered on exit, so the autoscaler terminates the
  pod on its next tick (≤`tick_seconds`).

A **nonzero** exit deliberately skips self-terminate: RunPod's restart is free crash recovery.
For a manual pod test with the autoscaler off, either put `RUNPOD_API_KEY` in the template env
(watch for "self-terminate accepted") or kill the pod in the console when done.

## Autoscaler (queue-driven pods)

With `runpod.enabled` + `runpod.api_key` set and the workqueue on, the control plane runs a scaling
loop (`src/scaler/`): per tick it reaps dead pods and adds at most one pod per queue when the queue
is backed up. Workers own scale-DOWN: `IDLE_EXIT_SECONDS` (delivered at pod create) becomes the
claim long-poll window, and a null claim means "queue stayed empty that long" → the worker
deregisters and exits 0.

Settings block (`settings.json` → `runpod`; env: `RUNPOD_ENABLED`, `RUNPOD_API_KEY`,
`RUNPOD_NETWORK_VOLUME_ID`, `RUNPOD_CP_URL`):

- `cp_url` — the control-plane URL pods dial back to; must be reachable from RunPod (funnel URL,
  not localhost).
- `tick_seconds` (15) — scaling-loop cadence. `stale_worker_seconds` (180) — a worker row silent
  this long is dead.
- `queues.<name>` — per-queue policy: `template_id`, `gpu_type_ids` (a PRIORITY-ORDERED list: the
  scaler creates with the first entry alone, and retries with the whole list only if RunPod refuses
  — asking for all of them at once gets whichever card RunPod prefers to hand out, and the cards are
  not substitutes, since ninfer serves only a 5090),
  `max_workers`, `scale_up_depth_per_worker` (add when pending ÷ effective workers hits this),
  `scale_up_max_age_seconds` (starvation trigger), `cooldown_seconds`, `idle_exit_seconds`
  (linger tuning: raise for chatty queues, 0 = never exit), `boot_deadline_seconds` (a pod this
  old with no worker row is reaped as wedged). The `queues` dict in `settings.json` replaces the
  default wholesale — carry complete blocks.
- Scale-from-zero fires on ANY pending job with no cooldown; a booting pod counts as capacity, so
  a 5-minute boot can't trigger add-forever.

**One-time manual check (unverified RunPod detail):** whether create-time `env` *merges with* or
*replaces* the template's env. The scaler passes the full worker env at create either way, but on
the first autoscaled pod confirm `CP_URL`/`WORKER_TOKEN`/`IDLE_EXIT_SECONDS` actually landed:
pod console → `printenv`.

First full cycle to watch (mesh, `max_workers: 1`): enqueue a mesh job → pod appears in the RunPod
console → worker row registers → job done → queue drains → worker exits + deregisters → pod
disappears (self-terminate or reaper within `stale_worker_seconds`).

---

## Known deferred risks (accepted for private alpha — trusted testers)

Flagged in the pre-open security audit; hardened 2026-07-23. Task breakdown + status:
`tasks/platform_polish.md` P1.

- **Untrusted generated JS in the browser** — CONTAINED, not isolated. Every `/play` response
  carries a CSP (`api/app.py` `_PLAY_CSP`) pinning scripted loads + network to this origin, with
  `object-src`, `base-uri`, `frame-ancestors` and `form-action` all `'none'`: generated code can't
  pull external scripts, and fetch/XHR/WS can't leave. Exfiltration is NOT fully closed — no CSP
  directive governs top-level navigation, so a `location =` to an external URL still leaves. The
  game shares the app origin, and therefore its localStorage, where the session bearer token lives;
  what makes that safe today is the ownership gate on `/play/games/<id>`, so a game only runs in its
  OWNER's browser and the token it can read is already its own. The first non-owner view — a share
  link, a storefront, or an admin bypass — makes it account takeover. TRUE origin isolation is
  REQUIRED before any game-sharing feature ships — see `tasks/platform_polish.md` P1 for the shape
  and the verified constraints.
- **Login throttle is the only rate limit.** `src/auth/ratelimit.py` caps online password guessing
  per handle (429 + `Retry-After`). Nothing else is capped: there is no conversational surface,
  builds charge credits before enqueue, every GPU job admits against the game's compute budget, the
  autoscaler absorbs depth, and accounts start at 0 credits — so build load is paid load, not an
  attack.
- **API surface closed.** `/docs`/`/redoc`/`/openapi.json` exist only under `MAESTRO_DEV=1`;
  session TTL is 7 days.
