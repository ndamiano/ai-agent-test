# Deploy runbook — private alpha

## Containerized deploy

The container path is the alternative to the manual runbook below: one Docker image runs the app
(API + SPA, same-origin, single uvicorn worker). Everything is parameterized by `.env` — no code
edits to deploy.

**One decision, one container.** The image is CPU-only. It is a *client* of the GPU services
(LM Studio `:1234`, ComfyUI `:8188`, TTS `:8880`, Trellis `:8189`) — those stay on the host and are
reached over `host.docker.internal` (compose wires `extra_hosts: host-gateway`). They are **not**
containerized here.

### Files

- `Dockerfile` — multi-stage: node builds `frontend/dist`, then a `python:3.12-slim` runtime installs
  deps + engine runtime libs, copies `run.py` + `src/` + the built SPA, and runs as non-root.
- `docker-compose.yml` — the single `app` service (build, `.env`, named volume, engine bind mounts,
  healthcheck).
- `.env.example` — every knob; copy to `.env` and edit.
- `scripts/provision.sh` — one-time host setup.
- `scripts/deploy.sh` — ship dev → prod.
- `.githooks/post-commit` — a commit-time reminder (does **not** build/deploy).

### The data invariant (critical)

Durable state — `runs/` and `private/auth.db` (user accounts + games + the credit ledger) — lives at
`WORKING_DIRECTORY=/data`, a path **outside** the source tree, backed by the **named Docker volume
`maestro-data`**. It survives image rebuilds and `deploy.sh` runs. `rsync` in `deploy.sh` never
touches it (it's not in the tree). Never point `WORKING_DIRECTORY` off `/data`, and never `docker
volume rm maestro-data` — that wipes every account and game.

### Host-mounted engines (required at build time, NOT baked into the image)

The build must produce runnable executables, so both engines are needed — but they are bind-mounted
read-only from the host, not baked in:

- **Ren'Py SDK** → mounted at `/opt/renpy`; the code reads env `RENPY_SDK=/opt/renpy`. Host path in
  `.env` var `RENPY_SDK_HOST`.
- **Godot binary** → mounted onto `/usr/local/bin/godot` (the code finds it via `which godot`). Host
  path in `GODOT_BIN_HOST`.
- **Godot export templates** (version-matched, separate large dir) → mounted under
  `~/.local/share/godot/export_templates/` (HOME is `/home/maestro` in the image). Host path in
  `GODOT_TEMPLATES_HOST`. The template dir **must** match the Godot binary version or native export
  fails.

The image apt-installs the shared libraries these binaries dlopen (libgl1, libglib2.0-0, SDL2,
fontconfig, X libs, …) so `renpy.sh` and `godot` execute headlessly inside the slim container. The
exact library set is to be validated on the first successful build — extend it if lint/export report
a missing `.so`.

### Brand-new box (provision → configure → deploy)

On the **prod box**:

```bash
# 1. one-time host setup: Docker + compose, Ren'Py SDK, Godot binary + export templates
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

then re-run `deploy.sh` from dev. `deploy.sh` `rsync`s the source (excluding `venv/`, node_modules,
`.git/`, the working dir, `.env`, `settings.json`), runs `docker compose build && docker compose up
-d`, and curls prod `/healthz` — failing loudly if any step errors.

### Editing `.env` after the first boot

A plain `docker compose up -d` does **not** re-read a changed `.env` for an already-running container
— it keeps the env baked in at its last (re)creation, so your edit silently has no effect. Force it:

```bash
docker compose up -d --force-recreate
```

Then confirm the value actually landed: `docker compose exec app printenv <VAR>`. (This bites the
inference endpoints in particular — a stale `LMSTUDIO_BASE_URL`/`COMFYUI_ENDPOINT` yields connection
-refused against the old host/port while `.env` on disk looks correct.)

### Accounts (no signup — manual only)

The CLI lives at `/app/src/auth/cli.py`; the container WORKDIR is `/app`, so run it from `/app/src`:

```bash
docker compose exec -w /app/src app python -m auth.cli create <handle>   # prompts for a password
docker compose exec -w /app/src app python -m auth.cli grant  <handle> <n>
```

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
HTTPS. Manual accounts only. Inference (LM Studio) + assets (ComfyUI) stay on the same box.

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
   Point `lmstudio.base_url` / `comfyui.endpoint` at the local inference services.

2. **Lock the inference services to localhost.** The app calls LM Studio (`:1234`) and ComfyUI
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
   python -m auth.cli grant  <handle> <n>   # top up credits if needed (seed is 100)
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

These were flagged in the pre-open security audit and consciously deferred. Fix before public beta.

- **Single-GPU DoS.** The build queue (`api/build_queue.py`) has no per-user in-flight limit or max
  depth; run creation + freeze are free (only `build` costs 1 credit, `INITIAL_CREDITS=100`), and
  failed builds refund. One account can queue ~100 builds and monopolize the GPU. Mitigation for now:
  trusted users + watch the queue. Fix = per-user in-flight cap + bounded queue.
- **`.rpy` Python injection.** `renpy/ir_vn.py` interpolates effect variable names unescaped into
  Ren'Py `$` statements. A crafted build (or a human `edit_node`) can inject Python that runs when the
  *generated game* is played — a risk to whoever downloads and runs another user's game. Trusted
  testers who don't swap game files → low risk. Fix = validate/escape effect var names.
- **Session TTL 30 days, no rotation** (`auth/store.py`); **`/docs` + `/openapi.json` public**
  (`auth/deps.py` PUBLIC_PATHS) expose the API surface. Both hardening, not blockers.
