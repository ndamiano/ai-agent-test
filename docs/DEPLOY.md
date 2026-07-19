# Deploy runbook — private alpha

## Containerized deploy

The container path is the alternative to the manual runbook below: one Docker image runs the app
(API + SPA, same-origin, single uvicorn worker). Everything is parameterized by `.env` — no code
edits to deploy.

**One decision, one container.** The image is CPU-only — the control plane: API + SPA + the job
queue + the node-based build gates. GPU inference is done by **worker agents** (`worker/agent.py`)
that PULL jobs over `/worker` from wherever the GPUs live (home box, RunPod pod), authed by
`WORKQUEUE_TOKEN`. The asset backends (ComfyUI/TRELLIS) are still called directly — point
`COMFYUI_ENDPOINT`/`TRELLIS_ENDPOINT` at the GPU box (e.g. its tailscale IP) until they're queued.

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

Durable state lives on **two named Docker volumes**, both outside the rsync'd source tree:
- `maestro-data` → `/data`: `runs/` + `private/auth.db` (accounts, credit ledger) +
  `private/platform.db` (games, builds, jobs, events).
- `maestro-games` → `/app/runtime/games`: staged playable bundles served at `/play`.

Both survive image rebuilds and `deploy.sh` runs. Never point `WORKING_DIRECTORY` off `/data`, and
never `docker volume rm` either volume — that wipes accounts and games.

### Build toolchain

The codegen build gates run a Node toolchain (`tsc` + `esbuild`) against the game folder — no game
engines. The image bakes `node` plus `runtime/node_modules` (npm-ci'd in a linux build stage so the
platform-specific binaries resolve). Generated games are plain TypeScript bundled to JS and served
as static files at `/play`.

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
- **Untrusted generated JS in the browser.** A build ships model-authored TypeScript bundled to JS
  and served at `/play` as static files. Playing another user's game runs their code in your browser;
  a crafted build could exfiltrate via the page's origin. Trusted testers who only play their own
  builds → low risk. Fix = sandbox the player (isolated origin / iframe + CSP).
- **Session TTL 30 days, no rotation** (`auth/store.py`); **`/docs` + `/openapi.json` public**
  (`auth/deps.py` PUBLIC_PATHS) expose the API surface. Both hardening, not blockers.
