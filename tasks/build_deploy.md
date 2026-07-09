# Build & Deploy System

## Why
There is no build/deploy pipeline — the app runs from a dev shell (`python run.py`, `npm run dev`)
with the inference stack started by hand. Public launch needs reproducible builds, CI on the test
suite, a deploy path, and persistence for the new user/credit data. Greenfield: no Dockerfile, no
compose, no CI exists today.

## Background (VERIFIED — updated 2026-07-09)
- **`.github/workflows/ci.yml` now exists** (T1 completed).
- **`Dockerfile` + `docker-compose.yml`** exist (T2 completed).
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
      (keep integration out of CI; it needs live services). Installs `pytest` and `httpx2` (for test client).
      Integration tests confirmed excluded. Test results: 791 passed, 2 skipped, 1 known limitation
      (test_health_and_login_are_public/"/" route in auth-gate unit test). Trigger: push/PR to master.
- [x] **Frontend build + typecheck** in CI — runs `npm ci`, `npm run build` (which runs `tsc -b && vite build`).
- [x] **Lint** — frontend ESLint via `npm run lint`.

## T2 — Containerize
- [ ] **Backend image** — FastAPI app + Python deps, reproducible.
- [ ] **Frontend build** — static build served/hosted (decide host in T4).
- [ ] **Inference stack images** — LLM server, ComfyUI, Trellis, TTS as buildable images. These are
      the **runpod worker images** for `scaleout.md` S3 — build once, use in both places.
- [ ] **Compose (or equivalent) for local/dev parity** — one command to bring the stack up instead of
      4 manual service starts.

## T3 — Persistence & config
- [ ] **Pick + provision the datastore** for users/credits/run-ownership (`auth_and_billing.md`
      depends on this — sqlite to start, a path to postgres). Migrations story.
- [ ] **Environment/secrets management** — prod settings + secret injection, replacing the ad-hoc
      `settings.json`.
- [ ] **Persistent volume for run dirs** (the artifacts) in a deployed environment.

## T4 — Deploy
- [ ] **Hosting decision** (see Parked) — own box / cloud VM / managed / runpod-for-everything.
- [ ] **One-command (or CI-driven) deploy** — build → push images → release.
- [ ] **Frontend hosting/CDN.**
- [ ] **Health checks + basic observability** (logs, build-worker status).

## T5 — Release hygiene
- [ ] **Versioning + tags**; a changelog (could be fed from `tasks/finished.md`).
- [ ] **Rollback path.**

## Ordering
T1 (CI) immediately — it's cheap and protects every other workstream. T2/T3 next (containerize +
persistence; T3 unblocks auth). T4 deploy once there's something to deploy. Share T2's inference
images with `scaleout.md` S3.

## Parked (needs owner input)
- **Hosting target:** self-hosted box vs cloud vs runpod-for-everything — drives T4 and the scaleout
  runpod design.
- **Datastore:** sqlite-to-start vs postgres-from-day-one for the user/credit store.
