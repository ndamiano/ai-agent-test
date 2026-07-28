# Deploy runbook — private alpha

## Containerized deploy

The container path is the alternative to the manual runbook below: one Docker image runs the app
(API + SPA, same-origin, single uvicorn worker). Everything is parameterized by `.env` — no code
edits to deploy.

**One decision, one container.** The image is CPU-only — the control plane: API + SPA + the job
queue. Every GPU backend is driven by **worker agents**
(`worker/agent.py`) that PULL jobs over `/worker` from wherever the GPUs live (home box, RunPod
pod), authed by `WORKQUEUE_TOKEN`. One worker process per queue:

```bash
python -m worker.agent --server http://<control-plane>:8000 --token <WORKQUEUE_TOKEN> \
    --queue llm   --target http://localhost:8080     # llama.cpp router
python -m worker.agent ... --queue image --target http://localhost:8188   # ComfyUI
python -m worker.agent ... --queue mesh  --target http://localhost:8189   # TRELLIS
```

The control plane never dials a GPU box — the queue is the only transport — so ComfyUI/TRELLIS can
stay bound to localhost and no endpoint setting exists on this side. A queue with no worker running
means every job on it times out, so all three workers are mandatory, not optional.

### Files

- `Dockerfile` — multi-stage: node builds `frontend/dist`, a second node stage resolves the mesh
  toolchain, then a `python:3.12-slim` runtime installs deps and copies `run.py` + `src/` +
  `runtime/` + the built SPA, and runs as non-root.
- `docker-compose.yml` — the single `app` service (build, `.env`, the two named volumes, the
  `settings.json` bind mount, healthcheck).
- `.env.example` — every knob; copy to `.env` and edit.
- `scripts/provision.sh` — one-time host setup.
- `scripts/deploy.sh` — ship dev → prod.
- `.githooks/post-commit` — a commit-time reminder (does **not** build/deploy).

### The data invariant (critical)

Durable state lives on **two named Docker volumes**, both outside the rsync'd source tree:
- `maestro-data` → `/data`: `runs/` (via `WORKING_DIRECTORY=/data`) + `auth.db` (accounts, credit
  ledger) + `platform.db` (games, builds, jobs, events) (via `MAESTRO_DATA_DIR=/data`, set in
  docker-compose.yml).
- `maestro-games` → `/app/runtime/games`: staged playable bundles served at `/play`.

Both survive image rebuilds and `deploy.sh` runs. Never point `WORKING_DIRECTORY` or
`MAESTRO_DATA_DIR` off `/data`, and never `docker volume rm` either volume — that wipes accounts
and games.

### Node in the image

Games are plain HTML/CSS/JS: staging copies the folder straight to `/play`. Node is in the image for
one subprocess — `runtime/decimate.mjs`, which cuts a finished TRELLIS GLB down to game weight. It
needs `runtime/node_modules` (gltf-transform + meshoptimizer), npm-ci'd in a linux build stage so the
platform-specific binaries resolve.

### Brand-new box (provision → configure → deploy)

On the **prod box**:

```bash
# 1. one-time host setup: Docker + compose (+ Node build toolchain for the container)
./scripts/provision.sh        # confirm the version vars at the top first
```

Then from the **dev box** (ships the source + builds + restarts on prod):

```bash
PROD_HOST=user@prod-box ./scripts/deploy.sh
```

The **first** deploy needs `.env` present on the prod box (deploy never ships it). On the prod box, in
the app dir (default `/opt/maestro`):

```bash
cp .env.example .env      # then edit: engine host paths, host.docker.internal endpoints
```

then re-run `deploy.sh` from dev. `deploy.sh` `rsync`s only what the box needs to build + run
(source, runtime, frontend, compose/Docker files, scripts — repo paperwork like docs/tasks/tests
and the pod-side worker Dockerfiles stay home), runs `docker compose build && docker compose up
-d`, and curls prod `/healthz` — failing loudly if any step errors. The control-plane image build
uses `Dockerfile.dockerignore`, which additionally drops `src/worker/` + `src/tools/
trellis_server.py` — pod-side code that never runs on the control plane.

### Editing `.env` after the first boot

A plain `docker compose up -d` does **not** re-read a changed `.env` for an already-running container
— it keeps the env baked in at its last (re)creation, so your edit silently has no effect. Force it:

```bash
docker compose up -d --force-recreate
```

Then confirm the value actually landed: `docker compose exec app printenv <VAR>`. (This bites
`CP_URL` and `WORKQUEUE_TOKEN` in particular — a stale value leaves workers unable to claim while
`.env` on disk looks correct.)

### Accounts (no signup — manual only)

The CLI lives at `/app/src/auth/cli.py`; the container WORKDIR is `/app`, so run it from `/app/src`:

```bash
docker compose exec -w /app/src app python -m auth.cli create <handle>   # prompts for a password
docker compose exec -w /app/src app python -m auth.cli grant  <handle> <n>
```

### GPU workers on RunPod

The control plane touches no GPU — every backend is a pull-side worker that dials out to it. One
container image per queue, weights on a RunPod **network volume** (the image is code, the volume is
weights), so a pod boots without re-downloading 60 GB.

```bash
# 1. one-time: populate the volume. Any cheap pod with it mounted; no GPU used.
VOL=/workspace bash scripts/provision_volume.sh

# 2. build + push the three worker images (one Docker Hub repo, queue-version tags)
docker build -f Dockerfile.worker-llm   -t ndamiano100/maestro-worker:llm-v4 .
docker build -f Dockerfile.worker-image -t ndamiano100/maestro-worker:image-v3 .
docker build -f Dockerfile.worker-mesh  -t ndamiano100/maestro-worker:mesh-v10 .
docker push ndamiano100/maestro-worker:mesh-v10   # etc.
```

Deployed tags: `llm-v3`, `image-v3`, `mesh-v10`. Bump the tag on every push — RunPod caches images
per host, so re-pushing a tag leaves stale copies serving on warm hosts.

**`llm-v4` is built but not pushed.** The llm image at `llm-v3` predates wire translation moving
into the worker, so it serves stale code; anything built from that Dockerfile before this fix
`ModuleNotFoundError`s on `llm_clients` at the first job, because only `src/worker` was copied.
Pushing it means bumping the llm queue's `template_id` to the new tag.

The mesh image is the fussy one; its runtime deps are the home-verified TRELLIS stack exactly
(see Dockerfile.worker-mesh): pinned transformers/timm/einops/kornia, the local TRELLIS.2 patch
set (`scripts/trellis2-sdpa-dinov3.patch` — sdpa attention backends + the DINOv3 module layout),
gcc for triton's first-use JIT of the flex_gemm kernels, and `TRITON_CACHE_DIR` on the network
volume so that JIT is paid once per volume, not per pod.

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

Make one RunPod **template** per image (container image + volume mount at `/workspace`; no ports).
Run each pod with the volume at `/workspace` and `CP_URL` + `WORKER_TOKEN` set (`WORKER_TOKEN` must
match `workqueue.token` on the control plane). No pod exposes a port: the inference server binds
`127.0.0.1`, since a reachable one is an unauthenticated GPU.

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

### Autoscaler (queue-driven pods)

With `runpod.enabled` + `runpod.api_key` set and the workqueue on, the control plane runs a scaling
loop (`src/scaler/`): per tick it reaps dead pods and adds at most one pod per queue when the queue
is backed up. Workers own scale-DOWN: `IDLE_EXIT_SECONDS` (delivered at pod create) becomes the
claim long-poll window, and a null claim means "queue stayed empty that long" → the worker
deregisters and exits 0.

Settings block (`settings.json` → `runpod`; env: `RUNPOD_ENABLED`, `RUNPOD_API_KEY`,
`RUNPOD_NETWORK_VOLUME_ID`, `RUNPOD_CP_URL`):

- `cp_url` — the control-plane URL pods dial back to; must be reachable from RunPod (funnel URL,
  not localhost).
- `queues.<name>` — per-queue policy: `template_id`, `gpu_type_ids` (a list),
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

### Enable the git hook (optional)

```bash
git config core.hooksPath .githooks
chmod +x .githooks/post-commit
```

It only prints a ship reminder after each commit — it never builds or deploys.

---

# Deploy runbook — private alpha (manual / Tailscale)

The private-alpha deploy: the app runs on the owner's GPU box as a **single uvicorn worker** that
serves both the API and the built frontend (same origin), fronted by **Tailscale Funnel** for public
HTTPS. Manual accounts only. Inference (the LLM server) + assets (ComfyUI) stay on the same box.

Everything below is parameterized by env vars — no code edits to deploy.

---

## One-time setup

1. **Backend deps + settings**
   ```bash
   cd <repo>
   python -m venv venv && source venv/bin/activate
   pip install -r requirements.txt        # if not already
   cp src/config/settings.example.json src/config/settings.json   # then edit
   ```
   No inference endpoints live in settings — every backend is named by its worker's `--target`.

2. **Lock the inference services to localhost.** The app calls the LLM server (`:1234`) and ComfyUI
   (`:8188`) as a client and never re-exposes them — but if *those* services bind `0.0.0.0`, opening
   the box exposes an unauthenticated GPU. Bind them to `127.0.0.1`, or firewall the ports:
   ```bash
   sudo ufw default deny incoming
   sudo ufw allow 41641/udp     # tailscale
   sudo ufw enable
   ```
   (Funnel reaches the app over the tailnet, not an open inbound port, so nothing else needs opening.)

3. **Tailscale + Funnel** (gives the public HTTPS URL + auto Let's Encrypt cert, no domain to buy):
   ```bash
   tailscale up
   # enable HTTPS certs + the Funnel node-attribute for this machine in the admin console / ACLs
   ```

---

## Build + launch (every deploy)

1. **Build the frontend** → `frontend/dist/` (the backend serves it automatically when present):
   ```bash
   cd frontend && npm ci && npm run build && cd ..
   ```

2. **Launch the backend** — production mode is the default (`MAESTRO_DEV` unset → no auto-reload,
   single worker, correct for the one GPU):
   ```bash
   source venv/bin/activate
   python run.py            # binds 0.0.0.0:8000, serves API + SPA same-origin
   ```
   Env knobs: `PORT` (default 8000), `HOST` (default 0.0.0.0), `MAESTRO_DEV=1` (local iteration only,
   turns reload back on). Same-origin serving means **CORS needs nothing set**; only set
   `MAESTRO_CORS_ORIGINS` (comma-separated) if you serve the SPA from a *different* origin.

3. **Expose it** publicly over HTTPS:
   ```bash
   tailscale funnel --bg 8000
   # -> https://<machine>.<tailnet>.ts.net
   ```
   That URL is the whole app: SPA at `/`, API under `/api` + `/auth`.

4. **Provision accounts** (no signup — manual only):
   ```bash
   cd src
   python -m auth.cli create <handle>       # prompts for a password
   python -m auth.cli grant  <handle> <n>   # grant credits (accounts start at 0)
   ```

---

## Smoke test (do this before handing out the URL)

```bash
BASE=https://<machine>.<tailnet>.ts.net
curl -s -o /dev/null -w "%{http_code}\n" $BASE/healthz          # 200
curl -s -o /dev/null -w "%{http_code}\n" $BASE/                 # 200 (SPA)
curl -s -o /dev/null -w "%{http_code}\n" $BASE/api/games/       # 401 (gated)
```
Then in a browser: log in as a test account → request a game → confirm it builds → download it.

---

## Redeploy (code update)

```bash
git pull
cd frontend && npm run build && cd ..     # only if frontend changed
# restart the backend process (Ctrl-C + relaunch, or `systemctl restart maestro`)
```
Funnel stays up across restarts; no re-expose needed.

### Optional: run under systemd (auto-restart on crash/reboot)
`/etc/systemd/system/maestro.service`:
```ini
[Unit]
Description=Maestro
After=network.target

[Service]
WorkingDirectory=/path/to/repo
ExecStart=/path/to/repo/venv/bin/python run.py
Restart=on-failure
Environment=PORT=8000

[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now maestro`.

---

## Known deferred risks (accepted for private alpha — trusted testers)

Flagged in the pre-open security audit; hardened 2026-07-23. Task breakdown + status:
`tasks/production_hardening.md`.

- **Untrusted generated JS in the browser** — CONTAINED, not isolated. Every `/play` response
  carries a CSP pinning all loads + network to this origin (`api/app.py _PLAY_CSP`): generated
  code can't exfiltrate or pull external scripts. It still shares the app origin — safe today
  because `/play/games/<id>` is ownership-gated, so a game only runs in its owner's browser.
  TRUE origin isolation (separate origin or SPA-seeded sandbox) is REQUIRED before any
  game-sharing feature ships — see production_hardening H1 for the verified constraints.
- **Chat rate-limited.** `POST /api/chat` is uncharged inference, so it's capped at 30
  turns/hour/user (429 + Retry-After). (The former "build-flood DoS" is retired: builds charge
  credits before enqueue, every GPU job admits against the game's compute budget, the autoscaler
  absorbs depth, and accounts start at 0 credits — a build flood is paid load, not an attack.)
- **API surface closed.** `/docs`/`/redoc`/`/openapi.json` exist only under `MAESTRO_DEV=1`;
  session TTL is 7 days.
