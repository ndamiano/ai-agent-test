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
  `docs/deploy.md`.

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

**What the ownership gate is actually holding up.** The session bearer token lives in the SPA's
`localStorage`, which is keyed by ORIGIN. Game code served from `/play/games/<id>/` is same-origin
with the SPA, so `localStorage.getItem(...)` hands it that token and `fetch('/api/...')` with it
acts as the viewer — an admin viewer reaches `require_admin` too. Today that is inert only because
a game runs solely in its owner's browser: it steals a token that already owns everything it can
reach. The instant ONE non-owner opens ONE game — a share link, a storefront, or an admin
convenience bypass in `_play_gate` — it is account takeover. There is no version of non-owner play
that is safe before this task lands, and an admin exemption is the SAME hole with a shorter path.

**The shape (chosen 2026-07-30): separate origin + framed harness + postMessage.** It also delivers
the console-capture feature (see the reporter item below), so the isolation work and the auto-fix
feature are one build, not two.

**Scheduling (decided 2026-07-31): DEFERRED, gated on the brand domain.** The game origin is a
separate registrable domain named `{brand}usercontent.com`, so the work starts once the brand name
is settled. A second Tailscale node (`games-<name>.<tailnet>.ts.net`) was evaluated as a no-domain
stopgap and DECLINED — the long-term shape is wanted, not an interim origin to migrate off.
Nothing forces the schedule while the owner is the only non-owner viewer: a specific game is played
by copying `runtime/games/<run_id>/` off prod and serving it locally on a port the dev SPA does not
use (a different port is a different origin, so the local copy cannot read a dev token).

Verified constraint (recon 2026-07-23): a bare `CSP: sandbox allow-scripts` breaks `/play` entirely
— the harness boots via dynamic ES-module imports (CORS-gated, `Origin: null` fails against the
header-less static mount) and the `SameSite=Strict` play cookie is not sent from an opaque-origin
document's subresources. That rules out the opaque-origin shape. The `srcdoc` + import-map +
authed-blob-URL alternative stays viable but needs the engine's `assetBase` fetches virtualized —
more code and more ways to be subtly wrong than a second hostname. Don't ship `ACAO: null` +
`SameSite=None` — that opens cross-site asset reads from any sandboxed context.

- [ ] **Game origin is a SETTING, not a constant** — `play.origin` in `settings.json`, read by the
      CSP builder, the handoff redirect, and the SPA's iframe `src`. First item because it un-blocks
      the whole task from the domain decision, and because the hosting shape below is expected to
      change once: moving game origins must be one settings value, never a code edit.
      → done when: no hostname/origin literal for games exists in `src/`; a settings change moves
      the game origin with no code edit.
- [ ] **Stand the game origin up on prod: `{brand}usercontent.com`, a SEPARATE registrable domain.**
      Cookies are domain-scoped, not origin-scoped, so any two names under one registrable domain
      share a cookie space and the game origin can TOSS a `Domain=`-scoped cookie onto the app
      (session fixation) even though `HttpOnly` stops it reading one. A distinct registrable domain
      removes that class; a subdomain of the app's domain does not. The game origin is the
      DELIBERATELY-UNTRUSTED origin, so it stays off the brand domain permanently.
      Prod is a droplet behind Tailscale Funnel (`docs/deploy.md:209,264`), which serves only
      `ts.net` names — so this means DNS → droplet IP, OPENING 443, and Caddy/nginx + Let's Encrypt,
      against a box where `ufw` allows only `41641/udp` (`deploy.md:232`) and nothing is directly
      internet-reachable. That inbound-exposure change is part of this item, not a footnote.
      Do NOT split by port instead: origin includes port so `localStorage` separates, but cookies
      ignore port and the jar stays shared — a half-fix.
      The rule is absolute either way: `maestro_play` and the play cookie NEVER carry a `Domain=`
      attribute (host-only today — `auth/router.py:29-38` — keep it).
- [ ] **Handoff: authenticate the PLAYER without a readable credential on the game origin.** The
      game origin must hold nothing game JS can read or replay elsewhere.
      1. SPA (app origin, holds the bearer) → `POST /api/games/<id>/play-token`; server runs the
         visibility/ownership predicate, returns a short-lived (~60s) single-use signed token.
      2. SPA sets the iframe `src` to `<play.origin>/handoff?t=<token>`.
      3. The game origin validates and 302s to the game's `index.html`, setting a cookie that is
         `HttpOnly` (JS cannot read it), host-only (no `Domain=`), `SameSite=Lax`, short max-age,
         and **`Path=/games/<slug>/`** — the path scope is what stops game A ambiently fetching
         game B's files with the viewer's credential.
      4. Sub-resource loads carry it automatically — this satisfies the "no header auth into /play"
         guardrail above rather than violating it.
      The token rides in a URL and lands in the game origin's access log; 60s + single-use makes
      that acceptable. An auto-submitting POST form removes even that if wanted.
      → done when: the app-origin bearer token is provably absent from the game origin, and a game
      cannot fetch another game's files with the viewer's cookie.
- [ ] **CSP deltas, both origins.** `frame-ancestors 'none'` in `_PLAY_CSP` (`api/app.py:122`) will
      BLOCK the new iframe — it must become `frame-ancestors <app origin>`, that origin only. On the
      game origin `connect-src 'self'` now resolves to a host with no API on it, which is the point.
      → done when: framing works from the app origin and is refused from any other.
- [x] **`form-action 'none'`** (2026-07-31, `api/app.py` `_PLAY_CSP`). `connect-src` governs
      fetch/XHR/WS only and `form-action` does not fall back to `default-src`, so a form POST to an
      external URL was permitted. Top-level navigation remains unrestricted (`navigate-to` never
      shipped) — exfiltration is narrowed, not closed, and only the ownership gate makes it
      unreachable. Add the same directive to the game origin's CSP when it exists.
- [ ] **Console reporter → the human-note fix path.** With the game cross-origin, the parent's
      `window.onerror` gets a scrubbed `"Script error."` with no file, line, or stack — the capture
      must live INSIDE the game document and `postMessage` out. Serve `index.html` on the game
      origin through a route that injects `<script src="/_harness/report.js">` into `<head>` on the
      way out: the model cannot be relied on to include the tag, and `staging.py` stays "no bundle,
      no transform" with the game folder pristine. The reporter hooks `window.addEventListener
      ('error', …, true)` (capture phase also catches failed `<img>`/`<script>` loads),
      `unhandledrejection`, and `console.error`/`warn`.
      Parent verifies `e.origin === <play.origin>` on every message and treats the payload as
      untrusted display data ONLY. The parent must never grow a branch that ACTS on a message from
      the child — that rebuilds the hole through a different door.
      Volume: a throw inside `requestAnimationFrame` emits ~60 identical errors/second. Dedupe on
      `(message, first stack frame)`, keep a count instead of repeats, ring-buffer with a hard cap.
      → done when: a game that throws produces a deduped error list in the parent, and a test
      asserts a `postMessage` from any other origin is ignored.
- [ ] **Human-gated auto-fix (the modal), one round.** Deduped errors → a modal: "we detected N
      errors — send to the model to fix? add notes" + send/close. Send routes into the EXISTING
      `fix_from_note` turn machine (the note is the error list plus the human's text); no new
      pipeline. Stays human-triggered: a console error is a legitimate signal under the CLAUDE.md
      rule (it detects BROKEN, and satisfying it IS the goal — one of the few that qualify), but the
      measured failure mode is the LOOP, not the signal — judge-then-fix rounds spent 208 of one
      build's 227 steps and scored WORSE in round 2 because each fix broke a claim that worked.
      Automatic firing only after the captured lists have been eyeballed across a battery, and
      capped at one round even then.
      → done when: the modal sends a fix note through `fix_from_note` and nothing fires without a
      human click.
- [ ] TRUE origin isolation, required BEFORE any game is viewable by a non-owner.
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

## P5 — Signed-in account in the UI — DONE 2026-07-31
- [x] The app header carries the signed-in handle next to the credit balance and the sign-out
      control, on every page.

## P6 — Release hygiene
- [ ] Versioning + tags; a changelog (could be fed from `tasks/finished.md`).
      → done when: `git describe --tags` returns a tag.
- [ ] Rollback path documented in `docs/deploy.md` and exercised once.
      → done when: `grep -in "rollback" docs/deploy.md` hits a procedure.

## P7 — Standing rules (no work until triggered)
- [ ] Any new uncharged inference path gets the same sliding-window cap as `/api/chat` (30
      turns/hour). Spec drafting rides chat today, so it is covered.
      → done when: triggered — a new uncharged path exists without a throttle.
- [x] CI lint step — `npx eslint .` exits 0 and `ci.yml`'s frontend job runs it before the build.
      The vitest suite is still NOT run in CI; only lint and build are.
- [ ] Inference-stack images (LLM/ComfyUI/Trellis/TTS). DEFERRED by design — engines are
      host-mounted over Tailscale for alpha; this becomes the RunPod worker image work when a
      fourth queue or a rebuild forces it.
      → done when: triggered — a worker image is needed that the current three don't cover.

## Parked
- Post-generation static scan of game bundles (defense-in-depth once P1 lands).
- Per-IP throttles at the reverse proxy (deploy config, not app code).
- Distributed vs single-box control plane — the queue works either way.
- Postgres (sqlite under `data_dir` is fine until it isn't).
