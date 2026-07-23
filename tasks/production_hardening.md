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

### H1 — Sandbox the generated game (the real one)
Model-authored `game.js` runs on the app origin; the SPA's bearer token lives in web storage on
that origin. A malicious or merely broken generation can read it and call the API as the user.
- [ ] Serve `/play` from an isolated origin (subdomain or sandboxed iframe with `sandbox=`
      no-same-origin + CSP; pick one — iframe is deployable without DNS work).
- [ ] Keep the `maestro_play` cookie auth working across the isolation boundary (it was built
      header-free for exactly this kind of embed).
- [ ] Test: a script inside a staged game cannot read the SPA's token storage or reach `/api/*`
      with ambient credentials.

### H2 — Chat abuse cap (the surviving DoS)
Chat requires a positive balance but never spends it: one $5 account can loop `POST /api/chat`
and burn llm-worker GPU forever at zero marginal cost. Platform pays per second; attacker pays once.
- [ ] Per-user rate limit on `/api/chat` (reuse the `auth/ratelimit.py` shape; N turns/hour is
      enough — real users draft a spec in a handful of turns).
- [ ] Same cap on any other uncharged inference path that appears later (spec drafting rides chat).
- [ ] Test: turn N+1 inside the window 429s; a build job is unaffected.

### H3 — Close the API surface
- [ ] Drop `/docs`, `/redoc`, `/openapi.json` from `PUBLIC_PATHS` and disable in the app
      constructor for prod (`docs_url=None`) — schema enumeration for free is a gift to attackers.
- [ ] Session TTL 30d → 7d + rotate token on login. (Cheap; do with H2.)

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
