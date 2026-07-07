# Auth + Billing — Login Gate & Credits

## Why
Before public launch the whole platform must sit behind **login + a credit balance**. Today there
is none — every run is anonymous and globally controllable. This workstream adds authentication,
per-user ownership, and a credit ledger that gates builds.

## Guardrails (READ FIRST)
- **DO NOT build self-serve account creation.** Accounts are created **manually** (admin/CLI) for
  now. Public signup is an explicit **launch-gate** task, deferred — do not add a signup route,
  form, or endpoint.
- **Real auth, not half-auth** (`CLAUDE.md` no-half-measures): a genuine session/token + middleware
  gating *all* routes, not a cosmetic check.
- **Credit deduct/refund must be atomic** — a build that fails to start must not burn credits.
- **Payments: manual grants only for now.** Build the ledger + a provider-agnostic seam; no Stripe/
  Paddle integration yet (owner's call). Design so a provider drops in without reworking the ledger.

## Background (VERIFIED — `file:line`)
- **No auth anywhere.** No user model, login, session/token, per-user scoping, credits, or billing
  in `src/` or `frontend/src/` (recon grep). The `Authorization: Bearer` at
  `src/llm_clients/openai_compatible_connector.py:253` is the outbound LLM key; the
  `type="password"` field at `frontend/src/components/SettingsPage.tsx:285` is the settings API-key.
- **Runs have no owner.** A run is an anonymous `uuid4` hex slug (`src/maestro/run.py:24-27`);
  `list_games` returns *all* runs on disk (`src/api/routers/games.py:103-134`); anyone can pause/
  cancel/download any run by id. No tenancy boundary.
- **Chat sessions are shared** — `_sessions` dict, default `session_id="default"`
  (`src/api/routers/chat.py:18-31`).
- **Persistence is on-disk run dirs only** — no database. Auth + credits introduce the first
  persistent user store (coordinate with `build_deploy.md` on the DB choice).

## T1 — Authentication  ✅ DONE
- [x] **User store + model** (id, handle, hashed password / token, created_at, role). sqlite store at
      `<working_dir>/auth.db` (`src/auth/store.py`); pbkdf2 passwords, sessions stored as token hash.
- [x] **Session/token + login endpoint.** `POST /auth/login` issues an opaque bearer token
      (`src/auth/router.py`).
- [x] **Gate ALL routes.** One app-level middleware (`auth.deps.install_auth`) requires a valid
      token on every http route (games, chat, outputs, settings, agents, system); the **WebSocket**
      authenticates itself via a `token` query param (`routers/websocket.py`). Public: `/`,
      `/auth/login`, docs.
- [x] **Manual account provisioning.** `python -m auth.cli create <handle>` (+ `passwd`, `list`).
      **No public signup route.**
- [x] **Per-user chat sessions.** `_sessions` keys on the authed user; the shared default is gone.
- [x] **Tests:** `test_auth_gate.py` (every router rejects anon; valid token passes; no signup route;
      ws gated), `test_auth_router.py` (login issues a working token), `test_auth_store.py`.

## T2 — Run ownership (also unblocks scaleout S1)  ✅ DONE
- [x] **Attach `user_id` to runs** at create — `create_run(user_id)` writes `owner.json`
      (`RunState.write_owner`); the chat tool path attributes via a user-id context var.
- [x] **Scope every run operation to its owner** — `_require_state(run_id, user)` in `games.py`
      404s an unknown run / 403s another user's; `list_games` filters to the caller. Closes the hole.
- [x] **Tests:** `test_run_ownership.py` + cross-user 403 cases across the games routers.

## T3 — Credit ledger
- [ ] **Balance + transaction log per user** in the store (grant / deduct / refund entries).
- [ ] **Deduct-on-build, refund-on-fail.** Gate `build_game` (`games.py:191`) on sufficient balance;
      deduct atomically at build start; refund if the build can't start / errors before producing.
- [ ] **Cost formula = one swappable function.** Start `cost(spec) -> 1` (flat). Keep it isolated so
      the metered/tiered variants (see the earlier decision) drop in later. Feed it usage from
      `scaleout.md` S3's metering hook when that lands.
- [ ] **Insufficient-credits response** the frontend can render (not a 500).
- [ ] **Tests:** build below balance is refused; a successful build deducts once; a failed-to-start
      build refunds; ledger entries reconcile.

## T4 — Buy credits (seam only, no integration yet)
- [ ] **Admin grant op** — `grant_credits(user, n)` via CLI (the "manual" path).
- [ ] **Provider-agnostic top-up seam** — a `credit_provider` interface + a webhook endpoint stub
      that credits the ledger on a verified purchase event. Leave the concrete Stripe/Paddle
      implementation as a TODO wired to this seam.
- [ ] **Tests:** a grant increments the balance + logs a transaction; the webhook seam credits on a
      (faked) verified event.

## T5 — Frontend gate
- [ ] **Login screen** + gate the app behind it.
- [ ] **Show balance**; surface "out of credits" on the build action.
- [ ] **No signup UI.**

## Ordering
T1 (auth) + T2 (ownership) first — they're the security floor and unblock `scaleout.md` S1. T3
(ledger) next. T4 (payment seam) last. Public signup + a real payment provider are the launch-gate
follow-ups, tracked separately when ready.

## Parked
- Session model (JWT vs server-side session) — decide at T1 impl.
- Self-serve signup + real payment provider — **launch gate**, deliberately out of scope now.
