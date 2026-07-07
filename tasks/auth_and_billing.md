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

## T3 — Credit ledger  ✅ DONE
- [x] **Balance + transaction log per user** in the store — `credits` column on `users` +
      `credit_transactions` (signed `delta`, reason, run_id); `balance` / `grant` / `deduct` /
      `refund` (`src/auth/store.py`). `deduct` is a single check-and-decrement UPDATE (atomic,
      never goes negative) returning a bool, not raising for control flow.
- [x] **Grant-on-create.** `create_user` seeds `INITIAL_CREDITS` and logs an `initial_grant` txn.
- [x] **Deduct-on-build, refund-on-fail.** `build_game` deducts atomically BEFORE enqueue; the
      queue worker refunds the exact deducted `cost` (carried on `_Item`) if the run is cancelled
      while queued or `run_build` raises. Exactly one net deduction per real build (cancel path and
      exception path are structurally exclusive — the cancel short-circuit sits outside the
      `run_build` try/except).
- [x] **Cost formula = one swappable function.** `src/auth/billing.py` `cost(spec) -> 1` (flat),
      isolated so metered/tiered variants drop in without touching the ledger or the gate.
- [x] **Insufficient-credits response** — clean HTTP **402** `{reason, balance, cost}` (never a 500,
      never enqueues).
- [x] **Tests:** `test_credits.py` (grant/deduct/refund, non-negative atomic deduct under two
      concurrent threads, ledger reconciles with balance), `test_games_router.py` (deduct-once /
      402-below-balance / refund-on-AlreadyQueued), `test_build_queue.py` (refund on cancel-while-
      queued + on `run_build` raising).

## T4 — Buy credits (seam only, no integration yet)  ✅ DONE
- [x] **Admin grant op** — `python -m auth.cli grant <handle> <n>` (the "manual" path), wired to the
      store's `grant(user_id, n, "admin_grant")`; unknown handle exits non-zero with a clean message.
- [x] **Provider-agnostic top-up seam** — `auth.credits` (`CreditProvider` ABC +
      `PurchaseEvent` + `get_provider`/`set_provider`; default `UnconfiguredProvider` refuses every
      event, so no unsigned credit path) + a webhook stub (`api/routers/billing.py`,
      `POST /api/billing/webhook`) that hands the raw body to the provider and, on a verified event,
      credits the ledger via `store.grant(..., "purchase")`. The concrete Stripe/Paddle verify is a
      `NotImplementedError` TODO wired to the seam.
- [x] **Webhook auth** — the path is registered **public** (`auth.deps.PUBLIC_PATHS`): a provider
      posts server-to-server with no user token, so it's authed by its signature (verified inside the
      `CreditProvider`), never by the user-token gate.
- [x] **Tests** (`test_billing_seam.py`): CLI grant increments balance + logs a txn; CLI grant on an
      unknown handle exits cleanly; the webhook credits on a stubbed verified event, rejects an
      unverified one (400, credits nothing), and is not blocked by the user-auth middleware.

## T5 — Frontend gate  ✅ DONE
- [x] **Login screen** + gate the app behind it. `AuthProvider` (`contexts/AuthContext.tsx`, token
      in `localStorage['maestro_token']`) wraps the app outside `WebSocketProvider`; `App` renders
      `LoginScreen` (handle+password → `POST /auth/login`, no signup) when there's no token, the app
      otherwise. The central client (`api/client.ts`) injects the bearer on all three fetch paths
      (`request`/`streamChatMessage`/`clearChatSession`) + the `?token=` WS query param + the
      browser-driven `asset-file`/`download` URLs (the auth gate now also accepts a `token` query
      param for those header-less GETs). A 401 from any call clears the token and drops back to login.
- [x] **Show balance** — `GET /auth/me` (new, gated) is the source; the header shows it (Layout),
      refreshed after every build. **Out of credits**: the client parses the 402 body into an
      `ApiError` and `GamesPanel.build()` surfaces `{reason, balance, cost}`.
- [x] **No signup UI.**
- [x] **Tests:** backend `test_auth_me.py` (anon 401, balance for a valid token, query-param auth);
      frontend `api/auth.test.ts` (bearer injected, 402 parsed not swallowed, 401 de-auths, token on
      asset/download URLs) + `components/AuthGate.test.tsx` (login gate when no token / app + balance
      when present).

## Ordering
T1 (auth) + T2 (ownership) first — they're the security floor and unblock `scaleout.md` S1. T3
(ledger) next. T4 (payment seam) last. Public signup + a real payment provider are the launch-gate
follow-ups, tracked separately when ready.

## Parked
- Session model (JWT vs server-side session) — decide at T1 impl.
- Self-serve signup + real payment provider — **launch gate**, deliberately out of scope now.
