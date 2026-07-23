# Production Hardening — Work Plan

The security/abuse deferrals that must close before public (paid) beta. Sourced from
`docs/DEPLOY.md` § Known deferred risks, re-assessed 2026-07-23 against the queue + credits
architecture. Ordered by real risk, not discovery order.

## Why

Pre-alpha ran on trust (a couple of known users). Paid beta doesn't. Each item below is a way an
untrusted account (or the code we generate for them) hurts us or other users. The old headline risk
— "one account floods the build queue" — is mostly RETIRED by architecture: builds are charged
before enqueue, every GPU job admits against the game's compute budget (`db/store.py enqueue_job`),
the autoscaler absorbs depth, and its oldest-pending-age trigger bounds starvation. With free
credits removed (accounts start at 0 — landed 2026-07-23), a 100-game flood is 100 purchases:
that's revenue plus a latency blip, not denial of service. What remains is smaller and listed here.

## Background (VERIFIED)

- `src/api/app.py` — `/play` StaticFiles mount (same origin as API + SPA); CORS credentials off,
  origins pinned; FastAPI default `/docs`, `/redoc`, `/openapi.json`.
- `src/auth/deps.py` — `PUBLIC_PATHS` whitelist; bearer header gate; `maestro_play` cookie
  (Path=/play, ownership-checked). `src/auth/store.py` — `SESSION_TTL_SECONDS = 30d`, no rotation.
- `frontend/src/contexts/AuthContext.tsx` — bearer token in web storage, same origin the generated
  game runs on. `GamesPanel.tsx` opens `/play/index.html?game=<id>` in a plain new tab.
- `src/api/routers/chat.py` — `require_credits` gate = positive balance only; a chat turn is never
  charged or metered (deliberate: no game exists to bill), and chat llm jobs carry no game_id so
  the compute budget doesn't gate them either.

## Tasks

### H1 — Sandbox the generated game — CONTAINMENT LANDED, isolation gates SHARING
Model-authored `game.js` runs on the app origin. Assessed 2026-07-23: the ownership gate means a
game only ever runs in its OWNER's browser, and stealing your own token gains nothing — so full
origin isolation blocks the SHARING feature (Stage 2 resale/public games), not paid beta.
- [x] Containment CSP on every `/play` response (`api/app.py _PLAY_CSP`): all loads + network
      pinned to this origin — generated code cannot exfiltrate anywhere or pull external
      scripts, eval blocked. Test: `test_play_auth.py`.
- [ ] TRUE origin isolation — required BEFORE any game is viewable by a non-owner. Verified
      constraint (recon 2026-07-23): a bare `CSP: sandbox allow-scripts` breaks /play entirely —
      the harness boots via dynamic ES-module imports (CORS-gated, `Origin: null` fails against
      the header-less static mount) and the `SameSite=Strict` play cookie is not sent from an
      opaque-origin document's subresources. Workable shapes: a separate origin (subdomain via
      DNS/proxy), or an SPA-seeded `srcdoc` sandbox with an import map over authed blob URLs
      (needs the engine's `assetBase` fetches virtualized). Don't ship `ACAO: null` +
      `SameSite=None` — that opens cross-site asset reads from any sandboxed context.

### H2 — Chat abuse cap (the surviving DoS) — DONE
Chat requires a positive balance but never spends it: one $5 account could loop `POST /api/chat`
and burn llm-worker GPU forever at zero marginal cost.
- [x] Per-user sliding-window limit on `/api/chat`: 30 turns/hour, 429 + Retry-After
      (`auth/ratelimit.py RequestThrottle`, wired in `api/routers/chat.py`).
- [ ] Same cap on any other uncharged inference path that appears later (spec drafting rides
      chat today, so it's covered).

### H3 — Close the API surface — DONE
- [x] `/docs`, `/redoc`, `/openapi.json` exist only when `MAESTRO_DEV=1` (app constructor +
      `PUBLIC_PATHS`); prod serves 404.
- [x] Session TTL 30d → 7d (`auth/store.py`). Login already mints a fresh token per session;
      revoking a user's other sessions on login was skipped on purpose (multi-device).

### H4 — Queue fairness polish (latency, not security — okay to defer past beta)
A legitimate burst (one user builds 20 games) raises everyone's latency until the scaler catches
up; `max_workers` caps the catch-up.
- [ ] Optional: round-robin claim by game_id (or user) instead of pure FIFO in `claim_job`, so one
      user's burst interleaves rather than head-of-line blocks.

## Guardrails
- No per-user build caps or queue-depth ceilings: paid load is wanted load — the credit charge IS
  the admission control. Only uncharged paths (chat) get rate limits.
- H1 must not reintroduce header auth into `/play` — sub-resource fetches can't carry headers;
  that's why the scoped cookie exists.

## Parked
- Post-generation static scan of game bundles (defense-in-depth once H1 lands).
- Per-IP throttles at the reverse proxy (belongs to deploy config, not app code).
