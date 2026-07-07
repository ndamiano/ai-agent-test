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

## Background (VERIFIED — current flow, `file:line`)

**Build trigger & execution:**
- `POST /games/{run_id}/build` → spawns a raw daemon `threading.Thread` running `run_build`
  (`src/api/routers/games.py:191-222`, thread at `:212-221`). Returns immediately.
- `_active_builds: set` + `_active_lock` (`games.py:90-91`) de-dupe only the **same** run_id
  (`:205-208`). **Different run_ids already run concurrently** — and collide on the singletons below.
- `run_build` runs synchronously on that bg thread (`src/maestro/run.py:30-82`); asset gen +
  packaging happen at the end on success (`:62-77`).
- `RunControl` registry (`src/maestro/run_control.py:77-97`) IS properly per-run isolated + locked —
  this piece is already concurrency-safe. Good foundation.
- No pool/limit on the per-build threads (`games.py:221`) — 100 builds = 100 threads.

**Collision points at concurrency (the work):**
- **LLM connector is a process-wide singleton** — `get_connector()` returns one shared instance
  (`src/llm_clients/connector_selector.py:7-9,24-53`); `reset_connector_cache()` mutates globals
  unlocked (`:17-21`).
- **Global LLM rate limiter** — `_llm_rate_limiter = LLMRateLimiter(rate=2.0, capacity=8)`
  (`src/llm_clients/rate_limiter.py:60-64`). One 2 req/s bucket shared across ALL builds → global
  starvation.
- **Settings is one global config** — `settings_manager` singleton (`src/config/settings_manager.py:118-119`);
  thread-safe but no per-user/per-run settings (endpoints, model all shared).
- **Single local inference server assumption** — one-model-resident LM Studio/llama.cpp with
  unload/reload eviction (`src/tools/comfyui_tools.py:732-874`); `vram_bracket()` (`:988-1009`) and
  `run_trellis_batch` (`:473-501`) free/unload models globally → concurrent builds evict each
  other's model mid-generation.
- **Single global asset endpoints** — ComfyUI `:8188` (`comfyui_tools.py:672`), Trellis `:8189`
  (`:452`), tile/mesh from the one settings singleton (`:447-449`).
- **WS event bus is a GLOBAL broadcast** — one `event_bus` singleton, single `asyncio.Queue(1000)`
  (`src/api/websocket/event_bus.py:86-87`), `broadcast_to_all` to every socket
  (`src/api/websocket/manager.py:22-37`); run isolation is **client-side only**
  (`frontend/src/contexts/WebSocketContext.tsx:86-93`). Every build's events leak to every browser.
- **Shared default chat session** — `_sessions` dict, default `session_id="default"`
  (`src/api/routers/chat.py:18-31`) — all sessionless clients share one `MainAgent`.

**Asset gen — sequential, at end:**
- `run.py:62-77` calls `generate_images` then `generate_voices` then `compile_for(...)` only after
  `result.ok`.
- `generate_images` (`src/renpy/fns.py:128+`) is plain sequential `for` loops (bg/char/cg/item/
  feature/emotion/tile passes); `run_jobs` (`comfyui_tools.py:1011-1024`) docstring literally
  "Run image jobs sequentially"; `generate_voices` is sequential per line. **No pool/executor/async
  anywhere.** Assets are defined up front and independent → fully parallelizable.

## Guardrails
- **Depends on auth** (`auth_and_billing.md`). Concurrency without ownership = anyone controls/
  downloads anyone's run (runs are anonymous `uuid4` slugs, globally listable — `run.py:24-27`,
  `games.py:103-134`). Land per-user run ownership alongside concurrent builds, not after.
- **No half-measures on the singletons** (per `CLAUDE.md`): make inference access per-run/per-tenant
  or explicitly pooled — do not bolt a lock around the global and call it done.
- Keep the engine-agnostic seam: asset parallelism must work for both renpy and godot asset passes.

## S1 — Concurrent multi-user builds (launch blocker)
Turn "raw threads against globals" into a bounded job/worker model with isolated inference access.

- [ ] **Build queue + worker pool.** Replace the per-request raw `threading.Thread` (`games.py:221`)
      with a bounded queue + worker pool (cap = how many builds the inference backend can serve).
      Excess builds queue with a visible "position N" state, not 100 contending threads.
- [ ] **Per-run inference handle, not a shared singleton.** Give each build its own connector /
      inference route (or a pooled lease) so `reset_connector_cache` and settings swaps can't yank
      a running build's connector. Decouple from the process-global (`connector_selector.py`).
- [ ] **Rate-limit / capacity per backend, not one global 2 req/s bucket** (`rate_limiter.py:60`).
      Size the limiter to actual backend capacity; scale it with the worker pool.
- [ ] **Per-run (and per-user) WS routing.** Filter server-side by run_id/user in the event bus
      (`event_bus.py`, `manager.py`) so a client only receives its own builds' events — today it's
      client-side cosmetic only. Fixes both the leak and the shared-queue bottleneck.
- [ ] **Per-session chat isolation.** Kill the shared `session_id="default"` (`chat.py:31`); key
      sessions to the authenticated user.
- [ ] **Tests:** two concurrent builds don't cross-contaminate state/events; queue caps at N;
      a settings change doesn't break an in-flight build.

## S2 — Parallel asset generation
Assets are defined once, independent, generated at the end — parallelize them.

- [ ] **Parallelize `generate_images`** (`renpy/fns.py`) — fan the independent jobs (backgrounds,
      characters, CGs, items, features, tiles) across workers instead of the sequential `for`.
      The img2img emotion pass depends on its neutral base → keep that dependency, parallelize across
      characters.
- [ ] **Make `run_jobs` concurrent** (`comfyui_tools.py:1011-1024`) — pool across available asset
      endpoints instead of one sequential loop on one endpoint. Requires >1 asset endpoint (→ S3).
- [ ] **Parallelize `generate_voices`** per line (independent TTS calls).
- [ ] **Reconcile with `vram_bracket`/`run_trellis_batch`** (`comfyui_tools.py:988-1009,473-501`):
      global VRAM juggling is incompatible with parallel gen on ONE box — parallelism needs multiple
      GPUs/endpoints (S3) or a scheduler that batches by model to avoid thrashing evictions.
- [ ] **Tests:** parallel asset gen produces the same manifest as sequential; failures isolate
      (one bad asset doesn't sink the batch); coverage reporting intact.

## S3 — On-demand inference (runpod)
Capacity is the ceiling for both S1 and S2. Move from one fixed local server to spin-up-on-demand.

- [ ] **Inference backend abstraction.** A backend registry / router that maps a build (or asset
      job) to an inference endpoint, so "localhost single server" becomes one backend among many.
      Generalize the single-endpoint assumptions (`comfyui_tools.py:672,452,447-449`,
      `connector_selector.py:42`) behind it.
- [ ] **Runpod spin-up/tear-down.** Provision GPU instances on demand (LLM + ComfyUI + Trellis/TTS
      stacks), register their endpoints with the router, tear down when idle. Warm-pool vs cold-start
      tradeoff is a design decision (note it).
- [ ] **Per-tenant routing + fair scheduling.** Route each user's build/assets to an available
      backend; don't let one big build starve others. Ties to S1's queue.
- [ ] **Cost metering hook.** Emit inference usage (tokens, GPU-seconds) per run — feeds the credit
      cost formula stub in `auth_and_billing.md`.
- [ ] **Tests:** router picks a healthy backend; backend loss mid-build fails gracefully (retry /
      requeue, not silent hang); idle tear-down doesn't kill an active build.

## Ordering
1. **Auth ownership first** (dependency) — see `auth_and_billing.md`.
2. **S1** to a mergeable point (queue + per-run isolation + WS routing) — the launch blocker.
3. **S3 backend abstraction** — the seam S1's queue and S2's pool both route through.
4. **S2 parallel assets** — lands cleanly once there's >1 endpoint to fan across.

## Parked
- Distributed vs single-box: is the API one box routing to runpod workers, or fully distributed?
  Decide at S3 design time — S1's queue/pool works either way.
- The `_active_builds` per-run guard (`games.py:90-91`) stays useful (no double-build a run) — keep it.
