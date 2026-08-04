# Platform Polish — the paid-beta engineering residue

Verified: 2026-08-03

Merged 2026-07-25 from `auth_and_billing.md` + `build_deploy.md` + `scaleout.md` +
`production_hardening.md`. The shipped work is in `finished.md` (payments, origin isolation and
the P1 harness all landed and were cut from here); what is left is polish and fairness, none of
it gating.

## Background (points at code — do not restate it here)
- Isolation: `src/api/app.py` (`_PLAY_CSP_BASE`, `_host_split`, `/handoff`), `src/auth/deps.py`
  (`_play_gate`), `src/auth/playgrants.py` (handoff tokens + per-game grant cookies).
- Inference: builds ride the worker-pull queue as jobs (`maestro/codegen/build_chain.py`); the
  one BLOCKING path is `queue_client.run_job` (asset prompt-merge), which reads the owning game
  off the `run_scope` contextvar.
- Scheduling: `src/db/store.py` `claim_job` (FIFO on `created_at`).
- Deploy: `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`, `scripts/deploy.sh`,
  `docs/deploy.md`.

## Guardrails (READ FIRST)
- **Signup stays invite-gated** until the owner drops the gate — a policy call, not a build.
- **No per-user build caps or queue-depth ceilings.** Paid load is wanted load — the credit charge
  IS the admission control. Only *uncharged* paths get rate limits.
- **`maestro_play` and the play cookie NEVER carry a `Domain=` attribute** (host-only — tested in
  test_play_auth.py), and the game origin stays a separate registrable domain, never a subdomain.
- Keep the queue a generic transport: it never learns what an asset is (`asset_chain.py` owns that).

## P1.5 — Stateless play grants (polish, deliberately deferred 2026-08-01)
- [ ] Grants live in memory (`auth/playgrants.py`), so every deploy/restart wipes live play
      sessions — a player mid-game gets "failed to load" until they press Play again. Owner's
      ruling: a refresh is an acceptable cost right now; fix when players are strangers. The
      shape: HMAC-signed `(user_id, run_id, expiry)` with a secret persisted under `data_dir` —
      the cookie carries the signed triple, no server table, restart-proof. Keep the handoff
      token single-use (that one is fine ephemeral; 60s).
      → done when: a play session survives `docker compose restart` without a re-click.

## P1.6 — Fix-round history in the UI (2026-08-02)
- [ ] Snapshots + restore exist (`snapshots.py`, CLI `--history`/`--restore`) — but only via ssh.
      Before strangers use fixes at all, the game page needs "try a fix, keep it or toss it":
      list the snapshots, one-click restore (re-stage included), current version marked. Restore
      is already non-destructive (the discarded round stays in git history), so the button is
      honest.
      → done when: a fix round can be reverted from the game page with no terminal.

## P1.7 — Engine visibility in the worker row (2026-08-02)
- [ ] The llm entrypoint picks ninfer or llama.cpp by card + driver and only the pod's boot log
      says which. The worker row records gpu_type but not engine, so the admin panel can't show
      that a pod is serving at fallback speed (62 vs 194 tok/s for the same rent). Needs a field
      through worker register → workers column → admin queues row, and an entrypoint edit —
      bundle with the next llm image bump, not its own.
      → done when: the admin queue card shows the engine beside the card, and a fallback pod is
      visibly a fallback.

## P1.8 — Error-gate probe egress (found 2026-08-03, before strangers)
- [ ] The gate's headless chromium runs user-steered game JS on the control-plane box with open
      network egress — a hostile game could probe the box or the cloud metadata endpoint from
      inside the probe. Origin isolation protects players; this is the server-side twin. The
      shape: block non-localhost requests in the probe context (playwright route interception —
      the game must reach only its own ephemeral static server).
      → done when: a game whose JS fetches an external URL logs a blocked request in the gate
      probe and the fetch never leaves the box.

## P3 — Inference-global residue
Build-as-jobs dissolved the original concern: a build holds no resident connector, each turn
re-reads settings when it builds its payload, and N concurrent builds are N independent job
chains. What remains is vestigial globals, not correctness:
- [ ] `_cached_connector` (`llm_clients/connector.py`) and the global 2 req/s
      `_llm_rate_limiter` (`llm_clients/rate_limiter.py`) predate the queue. Decide what each
      still means on the blocking path (`run_job`: the asset prompt-merge) and delete what
      doesn't.
      → done when: both names are gone or their remaining role is stated where they live.

## P4 — Fair scheduling
- [ ] Round-robin claim by game_id (or user) instead of pure FIFO in `claim_job`, so one user's
      hundred-job build interleaves rather than head-of-line blocks. Latency, not security — okay to
      defer past beta.
      → done when: a test enqueues user A ×20 then user B ×1 and B is claimed before A's tail.

## P6 — Release hygiene
- [ ] Versioning + tags; a changelog (could be fed from `tasks/finished.md`).
      → done when: `git describe --tags` returns a tag.
- [ ] Rollback path documented in `docs/deploy.md` and exercised once.
      → done when: `grep -in "rollback" docs/deploy.md` hits a procedure.

## P7 — Standing rules (no work until triggered)
- [ ] Any new uncharged inference path gets a throttle. The last uncharged path (chat) is gone —
      planning charges at Plan — so nothing is currently exposed.
      → done when: triggered — a new uncharged path exists without a throttle.
- [x] CI lint step — `npx eslint .` exits 0 and `ci.yml`'s frontend job runs it before the build.
      The vitest suite is still NOT run in CI; only lint and build are.

## Parked
- Per-IP throttles at the reverse proxy (deploy config, not app code).
- Distributed vs single-box control plane — the pins are enumerated in `docs/architecture.md`.
- Postgres (sqlite under `data_dir` is fine until it isn't; LISTEN/NOTIFY replaces the claim scan
  when it lands).
