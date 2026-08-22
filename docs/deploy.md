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
- `docker-compose.yml` — the `app` service (build, `.env`, the two named volumes, the
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

Surviving the BOX is `docs/backups.md`: a control-plane thread snapshots both DBs to the bucket
(every 15 minutes, sooner on account/credit writes), every settled run uploads its own archive
with a nightly `--archive-all` sweep behind it, and the restore drill there is the proof either
one works.

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

## Accounts (invite-gated signup, or manual)

Self-serve signup exists at `/login` ("Create an account") and is gated by invite codes: mint a
batch in the admin panel (`/admin` → Invite codes) or hand one out per person — a code carries
`max_uses` and can be disabled. Manual provisioning still works; the CLI lives at
`/app/src/auth/cli.py`; the container WORKDIR is `/app`, so run it from `/app/src`:

```bash
docker compose exec -w /app/src app python -m auth.cli create <handle>   # prompts for a password
docker compose exec -w /app/src app python -m auth.cli grant  <handle> <n>
```

Accounts start at 0 credits either way.

## Smoke test (do this before handing out the URL)

```bash
BASE=https://<prod-host>
curl -s -o /dev/null -w "%{http_code}\n" $BASE/healthz          # 200
curl -s -o /dev/null -w "%{http_code}\n" $BASE/                 # 200 (SPA)
curl -s -o /dev/null -w "%{http_code}\n" $BASE/api/games/       # 401 (gated)
```

Then in a browser: log in as a test account → request a game → confirm it builds → press Play on
the game page (the iframe rides the /handoff flow). The build needs an `llm` worker running or its
first turn times out.

## Public domains: gamesummoner.com + gamesummonerusercontent.com

Two REGISTRABLE domains, deliberately: the app lives on `gamesummoner.com`, games (model-authored
JS) are served from `gamesummonerusercontent.com`. Separate registrable domains — not a subdomain —
because cookies are domain-scoped, and a subdomain of the app's domain could toss a `Domain=`
cookie onto the app (session fixation). One process serves both hostnames; the host-split
middleware (`api/app.py` `_host_split`) makes each host serve ONLY its own surface: the game host
answers `/play` + `/handoff` + `/healthz` and 404s the rest (a game's `fetch('/api/…')` resolves
to a host with no API on it — that IS the isolation), and the app host 404s `/play`.

Serving them means direct inbound 443 — Tailscale Funnel serves only `ts.net` names. Box side is
DONE (2026-08-01): ufw active (OpenSSH + 80 + 443 + 41641/udp; it was inactive before, not the
41641-only posture this doc used to claim), caddy 2.6 from Ubuntu universe installed and running,
and the app container now binds `127.0.0.1:8000` (compose) — it was `0.0.0.0`, which serves the
app to the internet on :8000 because Docker's iptables chain bypasses ufw entirely. Funnel and
caddy both proxy via loopback, so nothing else changed.

`/etc/caddy/Caddyfile` as deployed — `default_bind` pins caddy to the PUBLIC IP because tailscaled
(Funnel) already owns 443 on the tailnet IP and a wildcard `:443` bind collides with it:

```
{
    default_bind 137.184.59.143
}
gamesummoner.com {
    reverse_proxy 127.0.0.1:8000
}
gamesummonerusercontent.com {
    reverse_proxy 127.0.0.1:8000
}
```

DNS (the one registrar-side step): A records for both apexes → `137.184.59.143`. Caddy is already
running and retries ACME on its own, so certs appear without a touch once DNS propagates. Then
point the app at the split in the bind-mounted `settings.json` (env: `MAESTRO_PLAY_ORIGIN` /
`MAESTRO_APP_ORIGIN`) and `docker compose restart` (settings load at process start):

```json
"play": {
  "origin": "https://gamesummonerusercontent.com",
  "app_origin": "https://gamesummoner.com"
}
```

Leave both empty (the default) and everything rides one origin — dev and the funnel-only alpha
deploy keep working unchanged. Split smoke test:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://gamesummoner.com/healthz               # 200
curl -s -o /dev/null -w "%{http_code}\n" https://gamesummoner.com/play/games/x/index.html   # 404 (no games on the app host)
curl -s -o /dev/null -w "%{http_code}\n" https://gamesummonerusercontent.com/api/games  # 404 (no API on the game host)
curl -s -o /dev/null -w "%{http_code}\n" https://gamesummonerusercontent.com/handoff    # 403 (alive, refuses a bare visit)
```

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
weights), so a pod boots without re-downloading the weights. Pods never talk to Hugging Face after
provisioning.

What the volume holds, by queue (`scripts/provision_volume.sh` is the authority):

| queue | weights | GB |
|---|---|---|
| llm | `models/ninfer/qwen3_8_27b_nvfp4.ninfer` (ninfer, a 5090 on r580+) + `models/LLM/qwen3.8_27b.gguf` (llama.cpp, any other card; stored under the model id because the router names a model by its file stem) | 37 |
| image | `checkpoints/NetaYume_v4_all_in_one` (sprites, scenes), `checkpoints/DreamShaperXL_Turbo_v2_1` (tiles, scene-chain terrain), `diffusion_models/qwen_image_2512_fp8_e4m3fn` (scene-chain subjects), `diffusion_models/qwen_image_edit_2511_fp8mixed` (the scene embed) + the `text_encoders/qwen_2.5_vl_7b_fp8_scaled` and `vae/qwen_image_vae` both Qwen graphs share, `RMBG/BiRefNet` (the matte) | 69 |
| mesh | `trellis2-weights` + `encoders/` (dinov3 mirror, BiRefNet) + the `hf-cache` pre-seed | 21 |
| image (safety) | `comfy/models/safety/` — the NSFW classifier | 0.02 |

An image checkpoint is named by a workflow in `src/config/workflows/`, so a weight that leaves
that folder leaves the volume with it: nothing else reads them.

The volume also speaks the **S3 API**, which is how a single file reaches it without renting
anything — a full provision is 60 GB at datacenter bandwidth and wants a pod, but one added or
retired artifact is a `cp`/`rm` from the home box:

```bash
aws s3 ls --profile runpod --region eu-ro-1 \
    --endpoint-url https://s3api-eu-ro-1.runpod.io s3://<volume-id>/ --recursive --human-readable
aws s3 cp --profile runpod --region eu-ro-1 --endpoint-url https://s3api-eu-ro-1.runpod.io \
    /var/tmp/ninfer-models/qwen3_8_27b_nvfp4.ninfer s3://<volume-id>/models/ninfer/
```

The bucket name IS the network volume id, and the region/endpoint pair is the datacenter the volume
lives in — a volume in another datacenter answers on its own endpoint or not at all.

```bash
# 1. one-time: populate the volume. Any cheap pod with it mounted; no GPU used.
VOL=/workspace bash scripts/provision_volume.sh

# 2. build + push the three worker images (one Docker Hub repo, queue-version tags)
docker build -f Dockerfile.worker-llm   -t ndamiano100/maestro-worker:llm-v9 .
docker build -f Dockerfile.worker-image -t ndamiano100/maestro-worker:image-v8 .
docker build -f Dockerfile.worker-mesh  -t ndamiano100/maestro-worker:mesh-v17 .
docker push ndamiano100/maestro-worker:mesh-v17   # etc.
```

Deployed tags (what the TEMPLATES name, checked live 2026-08-08): `llm-v8`, `image-v8`, `mesh-v17`.
`llm-v9` (Qwen3.8-27B NVFP4) is pushed to Docker Hub as of 2026-08-21 but the RunPod template still
names `llm-v8` until someone points it at the new tag — see "roll the volume forward first" above.
An image tag and the volume's weights go live in LOCKSTEP: the entrypoint stages and warms up on
the checkpoints the workflows name, so a pod predating a model swap dies at boot on a weight that
is no longer there. Roll the volume forward first, the template second, and retire the old weight
last (the 2026-08-08 flux → NetaYume/DreamShaper/Qwen swap, in that order).
Bump the tag on every push — RunPod caches images
per host, so re-pushing a tag leaves stale copies serving on warm hosts.

**The llm image carries BOTH engines and picks at boot.** ninfer serves the same 27B ~60% faster
but is compiled for `sm_120a`, so it runs on a 5090 and nowhere else — and the HOST DRIVER is part
of the capability: ninfer is a CUDA 13.1 build, which needs the host at r580+, and RunPod hosts
vary. On an older driver ninfer dies at `cudaGetDeviceCount` (`cudaErrorInsufficientDriver`) and
the pod boot-loops, billing until the boot-deadline reaper collects it — measured 2026-08-01, two
pods in a row. So the entrypoint (`llm-v7`) reads `nvidia-smi` name AND driver version: ninfer on
a 5090 at r580+, llama.cpp (the base image's own CUDA 12.8 build, fine on old drivers) on
everything else. Old-driver draws proved common (3 of 4 on 2026-08-02), so the scaler now sends a
create-time CUDA floor: `queues.<name>.allowed_cuda_versions` (prod llm: `["13.0"]` — RunPod's
"13.0" means an r580+ host, which runs the CUDA 13.1 ninfer via minor-version compatibility). The
floor rides only the HEAD gpu ask and is dropped when the create widens to fallback cards — those
serve the GGUF on any driver, and a slow pod beats no pod. The entrypoint driver gate stays as the
belt to this suspender. That is why the volume holds the model twice (`models/ninfer/*.ninfer` and
`models/LLM/*.gguf`, ~37 GiB together) and why an llm pod needs `LLM_MODEL` in its env: ninfer refuses any request whose `model`
is not its `--model-id`, and the autoscaler delivers the control plane's `llm.model` at create.
The ninfer build stage compiles a pinned commit of github.com/Neroued/ninfer — it needs CUDA 13.1
(the base image ships 12.8 for llama.cpp; only `libcudart.so.13` is added).

**The image worker carries the NSFW classifier; ship its weights before the control plane.** The
bundle comes from `python scripts/export_safety_model.py <dir>` (a box with HF access; ~22 MB:
`model.pt` + `config.json`) and lands on the volume as `safety/` — the entrypoint's symlink tree
puts it at `/opt/comfy-models/safety`, where `worker/safety_vision.py` reads it. A local image
worker reads the same layout from its own models dir (`SAFETY_MODEL_DIR` to point elsewhere).
Order matters on first rollout: volume weights + new image tag FIRST, control plane second — the
control plane refuses any render without a verdict, so old image workers under a new control
plane refuse every render.

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

**Mesh cold start (measured on 5090 pods, 2026-08-02, `mesh-v17`).** A pod reaches WARM — able to
serve at steady speed — in ~25s in-container (~60-70s with pod create + pull), from ~63s/~113s
before, and a claimed job never pays boot:

| phase | seconds | what removed the old cost |
|---|---|---|
| pod create → container running (13.9GB pull) | ~35-45 | image size — the remaining lever |
| stage 11.3GB volume → `/dev/shm` (ckpts + encoders) | ~6 | @~2GB/s; the FUSE mount does NOT retain page cache, so only loading from RAM holds. Encoders ride along since 2026-08-02 — they read at pipeline construct off the same mount |
| torch/trellis import | ~10 | overlaps staging |
| pipeline load | ~7 | skip default init (37s of a 43s load, all overwritten by the checkpoint) + load only the tier's 6 models, not all 8 |
| warmup mesh | ~11 | was ~52s: 38s of it was flex_gemm RE-AUTOTUNING every sparse-conv config per boot — its persistent cache defaulted to container disk. `FLEX_GEMM_AUTOTUNE_CACHE_PATH` now points at the volume: the first pod ever pays, every pod after loads (25KB json, keyed by device name). Real jobs stop paying 30-50s on unseen shape keys too — the cache accumulates |
| every real job | ~8-13 | |

The warmup's per-stage timers print on every boot (`[trellis] stage <name>: Ns`), and
`TRELLIS_WARMUP_TIMING=1` adds a second timed generate for boot profiling — the delta against the
first is the one-time cost, attributed. The triton kernel cache stays on the volume UNSTAGED: its
`.so` files are dlopen'd and tmpfs is noexec (measured: "failed to map segment" kills the warmup),
and its reads were never the cost.

The entrypoint gates worker registration on `/health` reporting `"warm": true`. That costs no
wall-clock (nothing can generate earlier) and keeps `exec_seconds` honest — otherwise the first
claimed job is billed 53s for 13s of work. It also means a pod that cannot generate dies at boot
instead of failing a user's job. Staging needs ~11GB free in `/dev/shm`, so mesh pods want ≥32GB
RAM; below that the server logs `staging skipped` and loads off the volume (~48s instead of ~4s).

**Image cold start (measured on a 5090 pod, 2026-08-02, `image-v6`).** Same disease the mesh had,
same cure: the FUSE volume serves bulk reads at ~2GB/s but mmap page-faults at ~200MB/s and keeps
no page cache, so ComfyUI's lazy checkpoint load put 70.3s of a checkpoint-off-the-volume INSIDE
the first claimed render — billed to a user's game as exec (measured on a prod batch, 2026-08-02).
The entrypoint now stages every weight the live workflows name, plus BiRefNet, into `/dev/shm` at
boot (~35s for 69GB, overlapped with
ComfyUI's own ~10s torch import), points the models tree at the staged copies (`/opt/comfy-models`,
per-folder symlinks; untouched folders still resolve to the volume), and runs one warmup render —
the real sprite graph at 1 step/256px, so the checkpoint load (3.8s from tmpfs), the matte's lazy
import and the first-use kernels are all paid before the agent starts. Registration is the warm
gate: container start → registered WARM once staging and the warmup have both landed, first claimed
render ~2s. The staging copy is now the boot cost rather than a free ride under the torch import —
four checkpoints at ~2GB/s, against one 17GB flux before. Staging falls back to the volume when
`/dev/shm` cannot hold it (logged `[stage] skipped`); a 5090 pod comes with ~126GB RAM, so 69GB of
weights still fits.

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

Flagged in the pre-open security audit; hardened 2026-07-23.

- **Untrusted generated JS in the browser** — contained everywhere, ISOLATED once `play.origin`
  is set. Every `/play` response carries a CSP (`api/app.py` `_PLAY_CSP_BASE`) pinning scripted
  loads + network to its own origin, framed only by the app origin: generated code can't pull
  external scripts, and fetch/XHR/WS can't leave. Exfiltration is NOT fully closed — no CSP
  directive governs top-level navigation, so a `location =` to an external URL still leaves. The
  play surface holds no readable credential in ANY mode (the grant cookie is HttpOnly and
  path-scoped to one game — see `auth/playgrants.py`), but with `play.origin` unset the game still
  shares the app origin's localStorage, where the session bearer token lives; the ownership check
  at play-session mint is what keeps that token the game's own. Before any game is viewable by a
  NON-owner, deploy the split domains (above) — on the split, game code runs on a host that serves
  no API and holds no app storage.
- **Login throttle is the only rate limit.** `src/auth/ratelimit.py` caps online password guessing
  per handle (429 + `Retry-After`). Nothing else is capped: there is no conversational surface,
  builds charge credits before enqueue, every GPU job admits against the game's compute budget, the
  autoscaler absorbs depth, and accounts start at 0 credits — so build load is paid load, not an
  attack.
- **API surface closed.** `/docs`/`/redoc`/`/openapi.json` exist only under `MAESTRO_DEV=1`;
  session TTL is 7 days.
