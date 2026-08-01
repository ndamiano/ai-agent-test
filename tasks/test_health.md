# Test Health — Audit & Anti-Bloat Governance

Verified: 2026-07-31

## Why
484 unit tests / 47 files, green in ~13s. Quality is high: near every file opens with a
contract-stating docstring, WHY-comments tie tests to real bugs/invariants, and asserts are
behavioural (charge-once, FIFO claim, budget reservation). The threat is **structural**: setup
boilerplate re-cloned per file is the path from 500 to 10,000 tests. Governance = shared fixtures
+ a truthful conventions doc + a cadence, NOT chasing coverage.

Surviving principles (do not re-litigate): variants are `parametrize` rows not cloned `def test_`
functions; every test carries a WHY-comment; test contracts not implementation; never cut a
validation/authz/money refusal test.

## Background (VERIFIED 2026-07-31)
- **Scale:** 484 tests / 47 files (+1 live `tests/integration/test_connectors.py`, correctly
  isolated). Full run ~13s. Run: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`.
- **Largest files:** `test_build.py` 37, `test_tools.py` 35, `test_workqueue.py` 25,
  `test_generate_media.py` 22, `test_tool_calls.py` / `test_safety.py` / `test_games_db_wiring.py`
  21 each. No file is a monolith.
- **Shared setup lives in `tests/conftest.py`:** `isolated_dbs` (autouse — both datastores under
  the test's tmp_path), `app_client` (`TestClient(app)`, no startup handlers), `tmp_runs`
  (run dirs under tmp_path), `anyio_backend` (asyncio only). `tests/fakes.py` holds
  `FakeResponse`. `tests/README.md` documents exactly these.
- **Everything is pytest**; async tests ride the anyio plugin (`@pytest.mark.anyio`), no
  pytest-asyncio.
- **Remaining per-file helpers, deliberately not folded:** `_user`/`_game`/`_make_game` in
  `test_play_auth`, `test_prompt_log`, `test_games_db_wiring`, `test_compute_budget`,
  `test_asset_chain`, `test_tools`. They are module-level functions with different returns (token
  vs headers, charged game vs game folder); a conftest factory would put a parameter on every
  signature that only needs a call. Fold them only if a third file wants the same shape.
- **Slowest items:** one 1.1s ws-routing test (a real 50×20ms poll), then six workqueue/admin
  tests at ~0.5s (long-poll windows, real-time by design). No `slow` marker needed yet.
- `parametrize` is in use where variants exist — no clone-fold backlog.

## Guardrails
- Governance only: no prod-code changes beyond a test seam, no coverage chasing.
- A conftest fixture earns its place by replacing ≥2 clones; don't invent speculative fixtures.
- Migrating a file to a shared fixture must not change what it asserts. Suite stays green at its
  current count (± only deliberate adds/cuts).
- Don't force divergent look-alikes into one `parametrize`; fold clones only.

## Tasks

### T1 — Shared conftest fixtures — DONE 2026-07-31
- [x] `isolated_dbs` (autouse; both datastores under `tmp_path`) — replaced the 12 `_tmp_db`
      clones. `test_workqueue`'s rate-limiter refill and `test_auth_ratelimit`'s throttle clear
      stayed local, as their own fixtures.
- [x] `app_client` + `tmp_runs` — replaced the 10 `TestClient(app)` clones.
- [x] `FakeResponse` in `tests/fakes.py` — replaced the two `_Resp` clones.
- [x] Deleted the 31 redundant per-file `sys.path.insert` shims.
- [ ] `make_user` / `charged_game` — not folded: the remaining helpers are module-level functions
      with divergent returns, so a fixture would add a parameter to every signature that only needs
      a call. Fold them if a third file wants the same shape.

### T2 — Rewrite `tests/README.md` truthfully — DONE 2026-07-31
- [x] Conventions + reviewer checklist kept; the fixture list is the real one, the count is
      current, `tests/fakes.py` documented.

### T3 — Framework unification — DONE 2026-07-31
- [x] `test_ws_event_routing.py` and `test_websocket_errors.py` are pytest, async via the anyio
      plugin (`anyio_backend` in conftest pins asyncio). Same assertions, same count.

### T4 — Re-audit cadence
- [ ] Each milestone (or ~+150 tests), rerun this recon: count/distribution, clone-grep
      (`grep -c "def _tmp_db\|TestClient(app)\|class _Resp" tests/test_*.py`), `parametrize`
      opportunities, README accuracy. This file's Background is the template; update it in place.
      → done when: pytest collects ≥634 tests (484+150) and Background above is updated in place

## Parked
- The workqueue long-poll wall-clock tests (~0.5s each) — real-time by design (they test the poll
  window); a clock seam is not worth the prod change today.
- The scroll-gate false positive is a PRODUCT issue, not a test-health one — do not "fix" it here.
