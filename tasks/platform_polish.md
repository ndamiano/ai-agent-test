# Platform Polish — the paid-beta engineering residue

Verified: 2026-07-25

Merged 2026-07-25 from `auth_and_billing.md` + `build_deploy.md` + `scaleout.md` +
`production_hardening.md`. Each had shrunk to 3–4 open items behind its own Why/Background/
Guardrails, and two of them owned the same queue-fairness task. The shipped work is in
`finished.md`; what is left is here, and it is all gated on **paid beta**.

## Why
Auth, ownership, credits, the compute budget, the job queue, the autoscaler, CI, containers and the
alpha deploy all landed. The residue is the part that only matters once strangers pay: a real
payment processor, isolation strong enough to share a generated game, release hygiene, and the
process-global inference state that concurrent builds still share.

## Background (points at code — do not restate it here)
- Payments: `src/auth/credits.py` (`CreditProvider` ABC; `UnconfiguredProvider` refuses every
  event), `src/api/routers/billing.py` (public webhook path), `src/auth/cli.py` (manual grants).
- Isolation: `src/api/app.py` `_PLAY_CSP`, `src/auth/deps.py` (`maestro_play` cookie, Path=/play,
  ownership-checked).
- Inference globals: `src/llm_clients/connector.py:240-255` (`_cached_connector`),
  `src/llm_clients/rate_limiter.py:68` (`_llm_rate_limiter`, one 2 req/s bucket for all builds).
- Scheduling: `src/db/store.py` `claim_job` (FIFO on `created_at`).
- Deploy: `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`, `scripts/deploy.sh`,
  `docs/DEPLOY.md`.

## Guardrails (READ FIRST)
- **No self-serve signup.** Accounts are created manually (`auth/cli.py`). Public signup is an
  explicit launch-gate task — do not add a route, form, or endpoint.
- **No processor integration until the owner picks one.** Build to the seam; never rework the
  ledger for a provider.
- **No per-user build caps or queue-depth ceilings.** Paid load is wanted load — the credit charge
  IS the admission control. Only *uncharged* paths get rate limits.
- **No half-measures on the singletons** (`CLAUDE.md`): make inference access per-run or explicitly
  pooled — do not bolt a lock around the global and call it done.
- **P1 must not reintroduce header auth into `/play`** — sub-resource fetches cannot carry headers;
  that is why the scoped cookie exists.
- Keep the queue a generic transport: it never learns what an asset is (`asset_chain.py` owns that).

## P1 — Origin isolation (gates SHARING, not beta)
Model-authored game code runs on the app origin. Containment landed (CSP pins every `/play` load and
network to this origin). The ownership gate means a game only ever runs in its OWNER's browser, so
full isolation blocks the Stage 2 sharing/resale feature — not paid beta.
- [ ] TRUE origin isolation, required BEFORE any game is viewable by a non-owner. Verified
      constraint (recon 2026-07-23): a bare `CSP: sandbox allow-scripts` breaks `/play` entirely —
      the harness boots via dynamic ES-module imports (CORS-gated, `Origin: null` fails against the
      header-less static mount) and the `SameSite=Strict` play cookie is not sent from an
      opaque-origin document's subresources. Workable shapes: a separate origin (subdomain via
      DNS/proxy), or an SPA-seeded `srcdoc` sandbox with an import map over authed blob URLs (needs
      the engine's `assetBase` fetches virtualized). Don't ship `ACAO: null` + `SameSite=None` —
      that opens cross-site asset reads from any sandboxed context.
      → done when: a game served to a non-owner runs from an origin that cannot read the API, with
      a test asserting the isolated document cannot reach `/api/games`.

## P2 — Payments
- [ ] Concrete `CreditProvider.verify` for the chosen processor.
      → done when: `grep -rn "NotImplementedError" src/auth/credits.py` is empty.
- [ ] Chargeback/refund clawback: revoke purchased credits (negative ledger entry — balance may go
      negative as the fraud marker) and suspend the account pending review. Credits being OUR ledger
      is the digital-goods advantage: a stolen-card purchase is reversible in-system even after the
      money is clawed back.
      → done when: `tests/test_billing_seam.py` covers verified-purchase-credits,
      chargeback-debits-and-suspends, and replayed-event-is-idempotent.

## P3 — Per-build inference isolation
- [ ] Per-run inference handle, not a shared singleton, so a settings swap cannot yank a running
      build's connector.
      → done when: `grep -n "_cached_connector" src/llm_clients/connector.py` is empty.
- [ ] Rate-limit per backend, sized to real capacity and scaled with the worker pool — not one
      global 2 req/s bucket shared by every build.
      → done when: `grep -n "_llm_rate_limiter" src/llm_clients/rate_limiter.py` is empty.
- [ ] Tests: two concurrent builds don't cross-contaminate state/events; a settings change doesn't
      break an in-flight build.
      → done when: those two cases exist as named tests and pass.

## P4 — Fair scheduling
Owned here; `scaleout.md` S3 and `production_hardening.md` H4 were the same item.
- [ ] Round-robin claim by game_id (or user) instead of pure FIFO in `claim_job`, so one user's
      hundred-job build interleaves rather than head-of-line blocks. Latency, not security — okay to
      defer past beta.
      → done when: a test enqueues user A ×20 then user B ×1 and B is claimed before A's tail.

## P5 — Signed-in account is invisible in the UI
- [ ] Show which account the browser is signed in as (top-left of the app shell), plus a sign-out.
      Today nothing on screen says it, so "which account is this build charged to" is a guess —
      and with manual account creation (`auth/cli.py`) a dev and a real account look identical.
      The client already fetches `/auth/me` (`frontend/src/api/client.ts:111`) — this is placement,
      not new API.
      → done when: the signed-in email (or handle) is visible on every page without opening
      devtools.

## P6 — Release hygiene
- [ ] Versioning + tags; a changelog (could be fed from `tasks/finished.md`).
      → done when: `git describe --tags` returns a tag.
- [ ] Rollback path documented in `docs/DEPLOY.md` and exercised once.
      → done when: `grep -in "rollback" docs/DEPLOY.md` hits a procedure.

## P7 — Standing rules (no work until triggered)
- [ ] Any new uncharged inference path gets the same sliding-window cap as `/api/chat` (30
      turns/hour). Spec drafting rides chat today, so it is covered.
      → done when: triggered — a new uncharged path exists without a throttle.
- [ ] CI lint step, once the frontend is lint-clean (52 pre-existing ESLint errors block it today).
      → done when: `cd frontend && npx eslint .` exits 0 AND `ci.yml` runs it.
- [ ] Inference-stack images (LLM/ComfyUI/Trellis/TTS). DEFERRED by design — engines are
      host-mounted over Tailscale for alpha; this becomes the RunPod worker image work when a
      fourth queue or a rebuild forces it.
      → done when: triggered — a worker image is needed that the current three don't cover.

## Parked
- Post-generation static scan of game bundles (defense-in-depth once P1 lands).
- Per-IP throttles at the reverse proxy (deploy config, not app code).
- Distributed vs single-box control plane — the queue works either way.
- Postgres (sqlite under `data_dir` is fine until it isn't).
