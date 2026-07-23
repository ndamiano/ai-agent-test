# Scale-Out — Concurrency, Parallel Assets, On-Demand Inference

## Why
Today the platform is single-user, single-box, one-build-at-a-time-in-practice. Public launch
means N users building at once (owner's stated worry: "100 people generating at once"). Three
coupled problems: (1) concurrent builds collide on shared singletons; (2) asset generation is
fully sequential at build-end when it's embarrassingly parallel; (3) inference capacity is one
local server — scale needs on-demand GPU (runpod). These share one spine: a **job/worker model +
per-tenant inference routing** instead of raw threads against global singletons.

**Priority (owner):** concurrent builds is the public-launch blocker. Parallel assets + runpod are
"future priority" — but they're the *relief valve* for concurrency (more builds need more inference
capacity), so design the spine now, fill it later.

## Background (VERIFIED — updated 2026-07-23; the spine LANDED)

**The job/worker spine exists and is the only transport to a GPU:**
- **Worker-pull job queue** — `src/db/store.py` (jobs + workers tables, atomic claim w/ lease,
  complete debits GPU-seconds) + `src/api/routers/workqueue.py` (claim/heartbeat/complete/
  deregister, token-gated) + `src/worker/agent.py` (the pull-side worker; one per queue: llm /
  image / mesh). No direct-call fallback anywhere.
- **Build-as-jobs** — a build is a CHAIN of `llm` jobs driven by
  `src/maestro/codegen/build_chain.py` (durable cursor in `runs/<id>/build_state.json`), not a
  resident thread. **Builds no longer serialize behind one another** — they contend only for llm
  workers, which autoscale.
- **Parallel asset generation** — `reskin.py` plans + gates, then `start_asset_chain` enqueues
  EVERY image job at once as one batch; `src/maestro/codegen/asset_chain.py` chains mesh
  follow-ups and finalizes the batch. Nothing waits on a GPU.
- **RunPod autoscaler live on prod** — `src/scaler/` (control plane owns scale-up + pod reaping;
  workers own scale-down via idle self-exit).
- **WS routing is per-user server-side** — event_bus resolves each event's run → owner (db
  `games.user_id`) and sends only to that user's sockets.

**Still-standing collision points (the remaining work):**
- **LLM connector is a process-wide singleton** — `get_connector()` returns one cached instance
  (`src/llm_clients/connector.py:240-255`), rebuilt on a settings change with no lock around the
  module global.
- **Global LLM rate limiter** — `_llm_rate_limiter = LLMRateLimiter(rate=2.0, capacity=8)`
  (`src/llm_clients/rate_limiter.py:68`). One bucket shared across ALL builds → global starvation.
- **No per-tenant fair scheduling** — the queue is FIFO per queue name; one user's hundred-job
  build can starve another user's single turn.

## Guardrails
- **Depends on auth** (`auth_and_billing.md`). Concurrency without ownership = anyone controls/
  downloads anyone's run (runs are anonymous `uuid4` slugs, globally listable — `run.py:24-27`,
  `games.py:103-134`). Land per-user run ownership alongside concurrent builds, not after.
- **No half-measures on the singletons** (per `CLAUDE.md`): make inference access per-run/per-tenant
  or explicitly pooled — do not bolt a lock around the global and call it done.
- Keep the queue a generic transport: asset parallelism rides the same job queue as llm/mesh work,
  and the queue never learns what an asset is (`asset_chain.py` owns that).

## S1 — Concurrent multi-user builds (launch blocker)
Turn "raw threads against globals" into a bounded job/worker model with isolated inference access.

- [x] **Build queue + worker pool.** DONE — and the interim `api/build_queue.py` (one-build-in-
      flight FIFO) is deleted, superseded by the real spine: the worker-pull job queue
      (`src/db/store.py` + `src/api/routers/workqueue.py` + `src/worker/agent.py`) + build-as-jobs
      (`src/maestro/codegen/build_chain.py`). Builds don't serialize behind one another any more;
      they contend only for llm workers, which autoscale.
- [ ] **Per-run inference handle, not a shared singleton.** Give each build its own connector /
      inference route (or a pooled lease) so a settings swap can't yank a running build's
      connector. Decouple from the process-global (`connector.py`).
- [ ] **Rate-limit / capacity per backend, not one global 2 req/s bucket**
      (`src/llm_clients/rate_limiter.py:68`). Size the limiter to actual backend capacity; scale it
      with the worker pool.
- [x] **Per-run (and per-user) WS routing.** Server-side filter: `manager.py` keys sockets by the
      authenticated user; `event_bus.py` resolves each event's run → owner (db `games.user_id`) and
      sends only to that user's sockets (no-run_id events fall back to a global broadcast). The leak
      is closed — a client only receives its own runs' events.
- [x] **Per-session chat isolation.** DONE — `src/api/routers/chat.py` keys `_sessions` on the
      authenticated `user_id`; the shared `session_id="default"` is gone.
- [ ] **Tests:** two concurrent builds don't cross-contaminate state/events; queue caps at N;
      a settings change doesn't break an in-flight build.

## S2 — Parallel asset generation  ✅ DONE (superseded shape)
The old sequential renpy `generate_images`/`run_jobs`/`generate_voices` path is deleted with the
engine it served. Assets now ride the shared job queue as one batch: `reskin.py` plans + gates,
`start_asset_chain` enqueues EVERY image job at once, and `asset_chain.py` chains each mesh
follow-up + runs the batch finalize (stage_for_play + assets_done). Parallelism = however many
image/mesh workers the scaler runs; a queue owns its GPU, so there's no VRAM juggling to reconcile.

## S3 — On-demand inference (runpod)
Capacity is the ceiling for both S1 and S2. Move from one fixed local server to spin-up-on-demand.

- [x] **Inference backend abstraction.** DONE — the worker-pull queue IS the seam: the control
      plane touches no GPU, workers dial out and claim, so "localhost single server" is just a
      worker registration like any pod's.
- [x] **Runpod spin-up/tear-down.** DONE — `src/scaler/` (live on prod): scale-from-zero on any
      pending job, workers self-exit on idle, the scaler's reaper is the billing guarantee.
- [ ] **Per-tenant routing + fair scheduling.** Route each user's build/assets fairly; don't let
      one big build starve others. The queue is FIFO today — still open.
- [x] **Cost metering hook.** DONE — `complete_job` debits measured exec_seconds to the owning
      game (`games.seconds_used`) + worker `busy_seconds`; the compute budget admits jobs against
      the grant (see `auth_and_billing.md`).
- [x] **Tests:** `test_gpu_queue.py`/`test_workqueue.py` (claim/lease-lapse requeue, stale
      completion dropped), `test_scaler_policy.py`/`test_scaler_stats.py`/`test_runpod_client.py`.

## Ordering
1. **Auth ownership first** (dependency) — see `auth_and_billing.md`.
2. **S1** to a mergeable point (queue + per-run isolation + WS routing) — the launch blocker.
3. **S3 backend abstraction** — the seam S1's queue and S2's pool both route through.
4. **S2 parallel assets** — lands cleanly once there's >1 endpoint to fan across.

## Parked
- Distributed vs single-box: is the API one box routing to runpod workers, or fully distributed?
  Decide at S3 design time — S1's queue/pool works either way.
- The `_active_builds` per-run guard (`games.py:90-91`) stays useful (no double-build a run) — keep it.
