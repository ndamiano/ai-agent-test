# Build & Deploy System

## Why
There is no build/deploy pipeline — the app runs from a dev shell (`python run.py`, `npm run dev`)
with the inference stack started by hand. Public launch needs reproducible builds, CI on the test
suite, a deploy path, and persistence for the new user/credit data. Greenfield: no Dockerfile, no
compose, no CI exists today.

## Background (VERIFIED — updated 2026-07-09)
- `Dockerfile` + `docker-compose.yml` + `scripts/deploy.sh` + `docs/DEPLOY.md` exist (T2/T3/T4).
- `.github/workflows/ci.yml` exists (T1): unit suite + frontend lint/build on push/PR to master.
- **Current run recipe** (`CLAUDE.md` + `tasks/nicknotes.md`): `source venv/bin/activate && python
  run.py` (backend), `cd frontend && npm run dev` (frontend), plus manual services: `comfy-start`,
  `docker start kokoro` (TTS), and a `llama-server` invocation for the LLM.
- **Recompile-a-run CLI** exists (`renpy.compiler.compile_renpy` / `godot.compiler.compile_godot`),
  but there's no packaging/release pipeline around the *platform*.
- **Settings** live in gitignored `src/config/settings.json` (copy of `settings.example.json`) —
  secrets management is ad-hoc.
- **Persistence today is on-disk run dirs only, no database.** Auth + credits (`auth_and_billing.md`)
  introduce the first user store — this workstream owns that DB choice + provisioning.
- Ties to `scaleout.md` S3: the runpod inference images are built artifacts this pipeline produces.

## Guardrails
- Reproducible builds; pin dependencies. No "works on my box" scripts that drift.
- Secrets never committed (settings.json already gitignored) — real secret handling in deploy.
- Don't hand-roll bespoke deploy scripts that duplicate what a standard tool does.

## T1 — CI (cheap, guards everything — do first) ✅ DONE
- [x] **Run the unit suite on push/PR** — `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`
      (keep integration out of CI; it needs live services). Installs `pytest` + `httpx` (test-only
      deps, kept out of requirements.txt) and bootstraps `settings.json` from the example. Trigger:
      push/PR to master.
- [x] **Frontend build + typecheck** in CI — runs `npm ci`, `npm run build` (which runs `tsc -b && vite build`).
- [ ] **Lint** — DEFERRED: 52 pre-existing ESLint errors; add the step once the frontend is
      lint-clean (revisit after the HITL component-browser rebuild replaces the old surface).

## T2 — Containerize  ✅ MOSTLY DONE
- [x] **Backend image** — `Dockerfile` (FastAPI app + Python deps).
- [x] **Frontend build** — static SPA served same-origin by the backend image.
- [ ] **Inference stack images** — LLM server, ComfyUI, Trellis, TTS as buildable images. DEFERRED
      by design: engines are **host-mounted** (compose `extra_hosts` + read-only mounts) and reached
      over Tailscale for alpha. Becomes the **runpod worker images** for `scaleout.md` S3 later.
- [x] **Compose for local/dev parity** — `docker-compose.yml` (one `app` service, host-gateway to the
      GPU stack, named data volume, healthcheck).

## T3 — Persistence & config  ✅ DONE
- [x] **Datastore provisioned** — sqlite at `private/auth.db` for users/credits/ownership
      (`auth_and_billing.md`). Path to postgres open when needed.
- [x] **Environment/secrets** — `.env` (`env_file` in compose); host engine paths + endpoints injected.
- [x] **Persistent volume for run dirs** — named `maestro-data` volume mounted at `/data` (runs/ +
      auth.db); survives image rebuilds (the critical data invariant, per `docs/DEPLOY.md`).

## T4 — Deploy  ✅ DONE (private-alpha tier)
- [x] **Hosting decision** — own/remote box, rsync + compose over Tailscale (`docs/DEPLOY.md`).
- [x] **One-command deploy** — `scripts/deploy.sh` (rsync source → remote → rebuild + restart container).
- [x] **Frontend hosting** — same-origin static serve (no separate CDN for alpha).
- [x] **Health checks** — `/healthz` + compose healthcheck. Deeper observability deferred.

## T5 — Release hygiene
- [ ] **Versioning + tags**; a changelog (could be fed from `tasks/finished.md`).
- [ ] **Rollback path.**

## Ordering
T1 (CI) immediately — it's cheap and protects every other workstream. T2/T3 next (containerize +
persistence; T3 unblocks auth). T4 deploy once there's something to deploy. Share T2's inference
images with `scaleout.md` S3.

## Parked (needs owner input)
- **Hosting target:** RESOLVED for alpha — self-hosted remote box over Tailscale (`scripts/deploy.sh`).
  Cloud/runpod-for-everything revisited at scale (`scaleout.md` S3).
- **Datastore:** RESOLVED — sqlite to start (`private/auth.db`); postgres path open for later.
