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

Durable state lives on **three named Docker volumes**, all outside the rsync'd source tree:

- `maestro-data` → `/data`: `runs/` (via `WORKING_DIRECTORY=/data`) + `auth.db` (accounts, credit
  ledger) + `platform.db` (games, builds, jobs, events) (via `MAESTRO_DATA_DIR=/data`, set in
  docker-compose.yml).
- `maestro-games` → `/app/runtime/games`: staged playable bundles served at `/play/games`.
- `maestro-demos` → `/app/runtime/demos`: the landing page's demo snapshots, served at
  `/play/demos`. A snapshot is a copy taken once; a rebuild, fix or change of the same run
  restages `runtime/games` and leaves the demo as it was.

Both survive image rebuilds and `deploy.sh` runs. Never point `WORKING_DIRECTORY` or
`MAESTRO_DATA_DIR` off `/data`, and never `docker volume rm` any of them — that wipes accounts and
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
./scripts/provision.sh        # one-time host setup: Docker + the compose plugin
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

## Accounts (open signup, or manual)

Self-serve signup is open at `/login` ("Create an account"), throttled per client IP. Manual
provisioning still works; the CLI lives at
`/app/src/auth/cli.py`; the container WORKDIR is `/app`, so run it from `/app/src`:

```bash
docker compose exec -w /app/src app python -m auth.cli create <handle> <email> [--role admin]   # prompts for a password
docker compose exec -w /app/src app python -m auth.cli grant  <handle> <n>
```

Demos are snapshots (`maestro/demos.py`): copy a staged game into the demos volume, then list its
id under `demo_games` in settings.json. `--replace` is the only way an existing snapshot changes.

```bash
docker compose exec -w /app/src app python -m maestro.demos snapshot <run_id> [--replace]
docker compose exec -w /app/src app python -m maestro.demos list
```

The other subcommands: `passwd <handle>` (reset a password), `email <handle> <email>` (change the
recovery address), `refund <handle> <n>` (a manual refund), `list`. Accounts start at 0 credits
either way.

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
DONE (2026-08-01): ufw active (OpenSSH + 80 + 443 + 41641/udp), caddy 2.6 from Ubuntu universe installed and running,
and the app container binds `127.0.0.1:8000` (compose) — a `0.0.0.0` bind would serve the app
to the internet on :8000, because Docker's iptables chain bypasses ufw entirely. Funnel and
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
point the app at the split in the bind-mounted `settings.json` — `play.origin` =
`https://gamesummonerusercontent.com`, `play.app_origin` = `https://gamesummoner.com`
(`docs/local_dev.md` "Settings") — and `docker compose restart` (settings load at process start).

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
    --queue llm   --target http://localhost:8090     # ninfer on the local 5090; :8001 on an RTX PRO 6000 pod
python -m worker.agent ... --queue image --target http://localhost:8188   # ComfyUI
python -m worker.agent ... --queue mesh  --target http://localhost:8189   # TRELLIS
```

`--target` is the worker's own inference server and should stay bound to `127.0.0.1` — a reachable
one is an unauthenticated GPU. `--worker-id` must name a row the scaler created for the pod: a
worker row carries the pod's price, and a claim from an id nobody created is refused.

A queue with no worker means every job on it times out.

## RunPod worker images

One image per engine, weights on a RunPod **network volume** (the image is code, the volume is
weights), so a pod boots without re-downloading the weights. The `image` and `video` queues share
one image — the same ComfyUI serves both — and a pod learns which it is from `WORKER_QUEUE`, set
by the scaler at create; it stages and warms that queue's weights alone. Pods never talk to
Hugging Face after provisioning.

There is more than one volume, because a volume pins its datacenter: the art volume
(`runpod.network_volume_id`, EU-RO-1, 5090-class hosts) holds the image and mesh weights, and
the LLM lives on its own volumes, one per datacenter it can be served from
(`queues.llm.network_volume_ids`, in order of preference). The scaler asks each datacenter for
the preferred card, then any card, and moves to the next volume only when a datacenter has
refused every card — so a capacity drought in one datacenter (US-NC-2 had two twenty-minute ones
on 2026-09-02) is a pod elsewhere rather than a wait. Surveyed 2026-09-02: the RTX PRO 6000
Server Edition is in nine datacenters, the cheaper and faster Workstation Edition only in EU-RO-1
(and now and then EUR-IS-1), and of those with volume support EU-RO-1 comes first.

What the art volume holds (`scripts/provision_volume.sh` is the authority):

| queue | weights | GB |
|---|---|---|
| image | `checkpoints/DreamShaperXL_Turbo_v2_1` (tiles, scene-chain terrain), `diffusion_models/qwen_image_2512_fp8_e4m3fn` (sprites, scenes, anim stills, mesh subjects), `diffusion_models/qwen_image_edit_2511_fp8mixed` (the scene embed) + the `text_encoders/qwen_2.5_vl_7b_fp8_scaled` and `vae/qwen_image_vae` both Qwen graphs share, `RMBG/BiRefNet` (the matte) | 58 |
| video | `diffusion_models/minimax_h3_fl2va_pruned_int8_convrot` (image-to-video), `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq`, `vae/minimax_h3_video_vae_fp16` — the anim sheets | 42 |
| mesh | `trellis2-weights` + `encoders/` (dinov3 mirror, BiRefNet) + the `hf-cache` pre-seed, and `kimodo/Kimodo-SOMA-RP-v1.1` (the motion model behind an anim's sheet — `CHECKPOINT_DIR` names this folder, so nothing resolves a hub name at run time) | 22 |
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
    <file> s3://<volume-id>/comfy/models/checkpoints/
```

A large `cp` (anything past ~10 GB) can end in `InvalidPart … N parts missing` from
`CompleteMultipartUpload` with the object already whole on the volume — the gateway races its own
part bookkeeping. Trust `head-object`'s `ContentLength` against the local size, not the exit code.
The bucket name IS the network volume id, and the region/endpoint pair is the datacenter the volume
lives in — a volume in another datacenter answers on its own endpoint or not at all (the llm
volume answers at `--region us-nc-2 --endpoint-url https://s3api-us-nc-2.runpod.io`).

**The llm volume holds the checkpoint PREPACKED**, not as safetensors. Qwen3.8 Flash-Next NVFP4
(`RadixArk/Qwen3.8-Flash-Next-NVFP4`, 126 GiB) loads through a repack pass that is CPU-bound —
~110 s on a fresh pod, NVMe or not — so the engine carries a `--load-format prepacked` loader that
restores the post-repack state from one flat file in 20–55 s (`docker/penny_patch.tgz`, applied
over the pinned fork tag). The pod boots from `models/pennyroyal/prepacked/`; the safetensors
shards are gone from the volume (the 142 GB quota holds one copy, not two) except one, because
the fork's namespace helper raises on a checkpoint with zero `*.safetensors`. The pack's key is
the load layout (quantization backends, tp, the config's hash) and not the path, so a second
volume in another datacenter is a COPY, not a re-dump. What worked (2026-09-02, EU-RO-1 from
US-NC-2, 130 GB in 9 minutes for $0.04 of CPU pod):

1. `hf download` the checkpoint to `models/pennyroyal/` on any pod with the new volume mounted
   (60 s at datacenter bandwidth), then DELETE every shard but one plus the index, over the
   volume's S3 endpoint. Keep the `.cache/huggingface` metadata the download wrote — without it
   the namespace helper hashes every byte it finds.
2. Rent a CPU pod IN THE NEW VOLUME'S DATACENTER with the volume mounted, `truncate` the pack
   file to its final size on the mount, and have 16 workers each `get-object --range` a 100 MB
   piece from the OLD volume's S3 endpoint and `dd … seek=` it into place. That is ~215 MB/s.

Two orders that do NOT work: shards and pack on the volume at once (the quota is one copy — a
full volume fails every write silently, the mount reports nothing, the S3 gateway keeps
answering stale sizes, and only a `put-object` from outside says `QuotaExceeded`); and writing
the pack THROUGH the S3 gateway (a single stream is ~15 MB/s, `UploadPart` over ~128 MB is 413,
48 concurrent parts is 524, and a 40-minute multipart session had lost a fifth of its parts by
`CompleteMultipartUpload`). A GPU pod that loads the checkpoint off the volume to dump a fresh
pack dies to the container cgroup; stage it to NVMe first if a re-dump is ever needed. A pod
launched by hand must not be named `maestro-<queue>-…` — the scaler reaps a pod under its prefix
that never registers a worker.

Beside the weights, every llm volume holds the ENGINE: `env/llm-env-<id>.tar` (~10 GB), the
python env, the fork checkout and the sm120 kernel caches that the llm Dockerfile builds and
exports rather than ships as layers, because they are mostly CUDA libraries that do not compress
and cost 90–120 s to pull on every host that had not seen the tag. The image is the toolchain
alone; the entrypoint restores the tarball into the container at boot. `<id>` is the tarball's
hash and the image carries it, so an image only ever boots against the tarball it was built
with — a new image means a new tarball on EVERY llm volume, uploaded before the template moves.
The fork is `ndamiano/sglang-rtxpro6000`; the pinned tag carries the prepacked loader over
jpezzulli's release. `fastboot/penny_cache_v6.tgz` on the US volume is the one build input the
Dockerfile still ADDs (kernel caches plus the serve script); fetch it into `docker/` first.

```bash
# 1. one-time: populate the art volume. Any cheap pod with it mounted; no GPU used.
VOL=/workspace bash scripts/provision_volume.sh

# 2. build + push the three worker images (one Docker Hub repo, queue-version tags)
docker build --target env --output type=local,dest=docker/out -f docker/Dockerfile.worker-llm .
ID=$(cat docker/out/env.id)                      # then docker/out/env.tar → every llm volume:
aws s3 cp --profile runpod --region eu-ro-1 --endpoint-url https://s3api-eu-ro-1.runpod.io \
    docker/out/env.tar s3://<llm-volume>/env/llm-env-$ID.tar   # ~8 min per volume from home
docker build -f docker/Dockerfile.worker-llm   -t ndamiano100/maestro-worker:llm-v19 .
docker build -f docker/Dockerfile.worker-image -t ndamiano100/maestro-worker:image-v11 .
docker tag ndamiano100/maestro-worker:image-v11 ndamiano100/maestro-worker:video-v3
docker build -f docker/Dockerfile.worker-mesh  -t ndamiano100/maestro-worker:mesh-v21 .
docker push ndamiano100/maestro-worker:mesh-v21   # etc.
```

The env export goes through `$DOCKER_TMPDIR` — default `/tmp` — at its full size; set it to a
disk with room. The Docker Hub repo is private, so every template carries the registry auth:
without it a pod exits in one second with no logs.

The deployed tag is whatever each RunPod TEMPLATE names; the templates are the only record of it
(`llm-v19`, `image-v11`, `video-v3`, `mesh-v21` at the last check). The `video` queue's template names its
own `video-*` tag — the same build as the image queue's, tagged twice, so the two templates roll
independently — from `video-v1` / `image-v9` on (ComfyUI 0.30.1, where the MiniMax-H3 nodes are
core). It needs no env of its own: `WORKER_QUEUE` arrives from the scaler. Both templates carry a
100 GB container disk: the pod stages its weights there (~42 GB video, ~58 GB image), and a stage
that does not fit is a silent 3.5-minute load off the FUSE mount (`[stage] skipped` in the log).
An image tag and the volume's weights go live in LOCKSTEP: the entrypoint stages and warms up on
the checkpoints the workflows name, so a pod predating a model swap dies at boot on a weight that
is not there. Roll the volume forward first, the template second, and retire the old weight last.
Bump the tag on every push — RunPod caches images per host, so re-pushing a tag leaves stale
copies serving on warm hosts.

**The llm image is one engine on one card.** Pennyroyal — jpezzulli's SGLang fork for the RTX
PRO 6000 — serves Qwen3.8 Flash-Next on the 96 GB SM120 card with vision, tool calls and a clean
reasoning split; it is the only stack measured to (2026-09-02, `docs/experiments.md`). So
`queues.llm.gpu_type_ids` names that card's two editions, Workstation first (faster; $2.19
against the Server Edition's $2.09 since 2026-09-06), Server second, and `allowed_cuda_versions` is the driver floor
its CUDA 13 wheels need (`["13.0"]`, RunPod's name for an r580+ host): a pod on an older driver
is dead on any card and bills until the boot-deadline reaper (`boot_deadline_seconds`, 300)
collects it. The floor rides every ask, including the widened one — there is no second engine to
fall back to. The image queue needs the same floor for the same reason (`image-v8` is torch
cu130).

An llm pod needs `LLM_MODEL`, `LLM_N_CTX`, `SGLANG_ARGS_EXTRA` and `WORKER_SLOTS` in its env, and
the autoscaler delivers them at create from the control plane's `llm.model`, `llm.n_ctx`,
`llm.sglang_args` and `llm.slots` — on prod those are the control plane's own `.env` (`LLM_MODEL`,
`LLM_N_CTX`, `SGLANG_ARGS`, `LLM_SLOTS`), which is why the settings.json there has no `llm` block. The engine serves under the name
`pennyroyal` at a 524288 window, both baked into its serve script; `LLM_MODEL` must be that name
and `LLM_N_CTX` — the control plane's input budget — at most that window, and the entrypoint
refuses to boot otherwise, since a request naming another model fails on the first turn. The
`sglang_args` string is appended to the launch line, so a tuning knob — graph batch sizes, draft
tokens, a sampler override — is a `settings.json` edit and the next pod, never a new image tag.
Two things to know before setting one: a flag that changes the FlashInfer autotune key costs
each fresh pod a ~300 s re-tune (the image bakes the cache for its own flags), and the image
appends `--cuda-graph-bs 1 2` LAST because builds run at most two streams and the later
occurrence wins.

Boot on the deployed stack (2026-09-03, EU-RO-1 Workstation Edition, prod scaler, create to
first claim): 205 s cold and 110 s warm with the engine in the image (`llm-v14`, 16.7 GB); 129 s
cold with the engine on the volume (`llm-v16`, 7.0 GB image plus a ~10 s tarball restore). The
pack restore is 35–55 s of any of those; provisioning and the pull are the rest. The first engine launch on a pod can still die to the
188 GB container cgroup while loading; the entrypoint gives it three attempts, and one has
sufficed on every measured pod since the loader stopped mmapping the checkpoint.

**The image and mesh workers carry the NSFW classifier; ship its weights before the control
plane.** The bundle comes from `python scripts/export_safety_model.py <dir>` (a box with HF
access; ~22 MB: `model.pt` + `config.json`) and lands on the volume as `comfy/models/safety/`.
The image entrypoint's symlink tree puts it at `/opt/comfy-models/safety`; a mesh pod mounts
the same volume and reads it at `/workspace/comfy/models/safety`, and refuses to boot without
it: every rendered sprite sheet is scored cell by cell on the pod that rendered it. `worker/safety_vision.py` reads either
from `SAFETY_MODEL_DIR`; a local worker reads the same layout from its own models dir.
Order matters on first rollout: volume weights + new image tag FIRST, control plane second — the
control plane refuses any render without a verdict, so old image workers under a new control
plane refuse every render.

**A schema change is an `ALTER TABLE` on the box before the deploy.** The store creates tables
with `IF NOT EXISTS` and never migrates, so a column added to a `CREATE TABLE` reaches a fresh
database only; on prod it is one statement through the container's python, run BEFORE
`deploy.sh` ships the code that writes it (2026-09-06, `workers.usd_per_hour` and
`workers.registered_at` — the backfill keeps every existing row live, not booting):

```bash
ssh maestro 'cd /opt/maestro && docker compose exec -T app python3 -c "
import sqlite3; c = sqlite3.connect(\"/data/platform.db\")
c.execute(\"ALTER TABLE workers ADD COLUMN usd_per_hour REAL\")
c.execute(\"ALTER TABLE workers ADD COLUMN registered_at REAL\")
c.execute(\"UPDATE workers SET registered_at = started_at\"); c.commit()"'
```

**`LLM_MODEL` has no default and the entrypoint refuses to start without one.** An llm pod that
boots without it (or with a name the engine does not serve) exits 1 — and RunPod restarts an
exited container and keeps billing until `boot_deadline_seconds` (300) expires, while the scaler
creates a replacement in the meantime. The autoscaler delivers it at create; a pod launched by
hand has to carry it in the template env.

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
The entrypoint now stages every weight the live workflows name, plus BiRefNet, onto the
container disk at boot (`/stage`, ~35s for 69GB, overlapped with ComfyUI's own ~10s torch
import), points the models tree at the staged copies (`/opt/comfy-models`, per-folder symlinks;
untouched folders still resolve to the volume), and runs one warmup render — the real sprite graph
at 1 step/256px, so the checkpoint load, the matte's lazy import and the first-use kernels are all
paid before the agent starts. Registration is the warm gate: container start → registered WARM
once staging and the warmup have both landed, first claimed render ~2s. The staging copy is now
the boot cost rather than a free ride under the torch import — four checkpoints at ~2GB/s,
against one 17GB flux before. Staging falls back to the volume when the disk cannot hold it
(logged `[stage] skipped`). It staged into `/dev/shm` until 2026-09-04, when a probe pod showed
RunPod caps `/dev/shm` at ~46 GB regardless of host RAM: neither queue's weights fit, every
image and video pod had been loading off the mount, and rent→warm was 3-4 min against the llm's
2. The container disk is a real filesystem whose page cache holds, so its size is the only cap,
and the templates set it at 100 GB.

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
is backed up. Workers own scale-DOWN: `IDLE_EXIT_SECONDS` (delivered at pod create) is the
worker's idle window — once every slot has been empty that long since its last job ended, the
worker deregisters and exits 0. The claim long-poll is capped server-side at 25 s, so the window
is the worker's own clock across polls (until 2026-09-04 a single null claim was taken as the
verdict, and every queue idled out at 25 s whatever the setting).

Settings block (`settings.json` → `runpod`, the full key list in `docs/local_dev.md` "Settings"):

- `cp_url` — the control-plane URL pods dial back to; must be reachable from RunPod
  (`https://gamesummoner.com`, not localhost).
- `tick_seconds` (15) — scaling-loop cadence. `stale_worker_seconds` (180) — a worker row silent
  this long is dead.
  A create RunPod refuses on every volume/card combination is recorded durably (`pod_refusals`,
  one row per refused scale-up, kept 30 days) with the combos tried and the provider's text;
- `queues.<name>` — per-queue policy: `template_id`, `gpu_type_ids` (a PRIORITY-ORDERED list: the
  scaler creates with the first entry alone, and retries with the whole list only if RunPod refuses
  — asking for all of them at once gets whichever card RunPod prefers to hand out, and the cards are
  not substitutes), `network_volume_ids` (the queue's own volumes, one per datacenter its weights
  are copied to, tried in order; absent, the queue rides `runpod.network_volume_id`),
  `allowed_cuda_versions` (the host-driver floor the queue's engine needs),
  `max_workers`, `cooldown_seconds`,
  `boot_seconds` (what a boot costs this queue — a flat estimate, never measured),
  `min_jobs_per_pod` (jobs a new pod must be owed when it lands, or it is not worth its boot),
  `idle_exit_seconds`
  (linger tuning: raise for chatty queues, 0 = never exit), `boot_deadline_seconds` (a pod this
  old that no worker has registered from is reaped as wedged). The `queues` dict in `settings.json` replaces the
  default wholesale — carry complete blocks.
- Scale-from-zero fires on ANY pending job with no cooldown. Past zero, a pod is added only when
  it is owed `min_jobs_per_pod` when it lands: the workers on hand (live and booting) eat
  `boot_seconds` ÷ seconds-per-job each while it boots, and what is left splits across the fleet
  it joins. Seconds per job is the last week's, from `jobs`, with the billing estimate standing
  in until the week has data; `boot_seconds` is configured and never measured, because a worker
  that registers under an id no create made a row for reads as an instant boot and dragged the
  average to a fraction of the truth. A booting pod counts as capacity, so a 5-minute boot can't
  trigger add-forever. Age triggers nothing — an old pending job means the fleet is still
  chewing, and the pod bought for it lands after that job is gone.
- `queues.video` is the image block with its own `template_id` (the `video-*` tag), the same cards
  and CUDA floor, and a longer `idle_exit_seconds` (90): an anim's sheet job lands ~20 s after
  its still renders, and a build asks for its characters together, so a video pod that exits on
  a short idle re-pays its boot for the next character.

Create-time `env` OVERRIDES the template's (verified 2026-09-04: every fleet pod dialed the
`cp_url` in settings while the templates still named the retired funnel host). The template env
is what a pod started by hand gets, so keep it current anyway.

First full cycle to watch (mesh, `max_workers: 1`): enqueue a mesh job → pod appears in the RunPod
console with its worker row already `booting` → the worker registers under the worker id it was started with → job done → queue drains → worker exits + deregisters → pod
disappears (self-terminate or reaper within `stale_worker_seconds`).

A run renting cards nobody wants is ended at its source, not in the console: a pending job is what
the scaler is answering, so terminating pods only buys the next tick. `POST /api/admin/games/<run
id>/stop` (admin token) ends any run whoever owns it — every job it holds on every queue fails and
the fleet drains itself. The owner's own `POST /api/games/<run id>/stop` does the same over their
own runs.

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
