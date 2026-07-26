# Test Health — Audit & Anti-Bloat Governance

Verified: 2026-07-25

Rewritten 2026-07-23. The previous file audited the pre-codegen-rewrite suite (IR/Ren'Py/Godot
era); those test files are deleted and its G1–G5 work is history — logged here only as the
principles that survived. This is a fresh recon of the CURRENT suite.

## Why
812 unit tests / 57 files, green in ~19s (recounted 2026-07-25). Quality is high: near every file opens with a
contract-stating docstring, WHY-comments tie tests to real bugs/invariants, asserts are
behavioural (charge-once, FIFO claim, budget reservation), and realistic fixtures exist
(`tests/fixtures/design_local_qwen_run2.txt` = captured model output; `tests/build_harness.py` =
the shared in-process build driver). The threat — same as last era — is **structural**: the suite
was rebuilt file-by-file with **zero shared fixtures** (`conftest.py` is a 4-line sys.path shim),
so setup boilerplate is re-cloned per file. That is the path from 600 → 10,000 tests. Governance =
shared fixtures + a truthful conventions doc + a cadence, NOT chasing coverage.

Surviving principles (do not re-litigate): variants are `parametrize` rows not cloned `def test_`
functions; every test carries a WHY-comment; test contracts not implementation; never cut a
validation/authz/money refusal test.

## Background (VERIFIED 2026-07-23; counts re-measured 2026-07-25 — nothing in T1–T3 has landed,
the suite just grew, which is the point)
- **Scale:** 812 tests / 57 files (+1 live `tests/integration/test_connectors.py`, correctly
  isolated). Full run 19s. Run: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`.
- **Distribution:** codegen build path 290/14 files (`test_codegen.py` alone is 70 tests /
  1178 lines), db/queue/scaler/worker 112/10, auth/billing/admin 59/11, config/utils 41/4,
  assets 39/3, api/ws/chat 39/5, connector/message-builder 38/5.
- **Cloned boilerplate (the lever):**
  - `_tmp_db` autouse fixture (`monkeypatch store._db_path → tmp_path`) cloned in **11 files**.
  - `TestClient(app)` fixture (auth.db + platform.db patch, sometimes `resolve_base_path`)
    cloned in **9 files** (`test_auth_gate/auth_me/admin_api/games_db_wiring/play_auth/
    workqueue/billing_seam/chat_credit_gate/safety`).
  - `_user` (create_user + grant + issue_token → headers) cloned in 3 files; the raw
    create_user/token boilerplate appears in **11 files** (34 call sites).
  - `_game` (create_game + charge_game) cloned in `test_compute_budget` + `test_asset_chain`;
    `_make_game` in `test_games_db_wiring` + `test_play_auth`.
  - Fake-requests `_Resp` class cloned in `test_gpu_queue` + `test_worker_agent`.
  - **36 files** carry a per-file `sys.path.insert` shim that `tests/conftest.py` already does.
- **`tests/README.md` is STALE/false:** advertises conftest fixtures that no longer exist
  (`spec_factory`, `run_state`, `frozen_run`, `patch_for_run`, `example_ir`, `ctx_factory`,
  `docs/examples/*.json` IR loaders) and says "582+ tests".
- **Two `unittest` holdouts:** `test_ws_event_routing.py` (`IsolatedAsyncioTestCase`) +
  `test_websocket_errors.py` (3 classes). Everything else is pytest. anyio 4.13 is installed
  (starlette dep) — its pytest plugin covers async migration; no pytest-asyncio.
- **New-surface shallow audit** (db_store, workqueue, gpu_queue, compute_budget, build_chain,
  worker_agent, scaler_*, codegen*): behavioural and well-motivated throughout. Grey zones, all
  tolerable: `test_codegen.py` imports 8 private names from `module.py` (`_authoring_order`,
  `_detect_typechecks`, `_is_stub`, `_kit_context`, …) — justified reach-in, don't expand; the
  6 workqueue long-poll tests each burn ~0.5s real wall clock; `test_worldgen_bridge` setup runs
  real worldgen (3.4s). Slowest 8 items ≤3.4s — no `slow` marker needed yet.
- `parametrize` is in use where variants exist (7 files; `test_scaffold` ×5, `test_auth_gate`
  GATED_PATHS) — no clone-fold backlog found in the current suite.

## Guardrails
- Governance only: no prod-code changes beyond a test seam, no coverage chasing.
- A conftest fixture earns its place by replacing ≥2 clones; don't invent speculative fixtures.
- Migrating a file to a shared fixture must not change what it asserts. Suite stays green at its
  current count (± only deliberate adds/cuts).
- Don't force divergent look-alikes into one `parametrize`; fold clones only.

## Tasks

### T1 — Shared conftest fixtures (the main lever)
Files: `tests/conftest.py` + the cloning test files. Verify: full suite green, no count change.
- [ ] `tmp_db` (autouse-compatible: patch `db.store._db_path → tmp_path/platform.db`) — migrate
      the 11 `_tmp_db` clones. Keep `test_workqueue`'s rate-limiter reset comment/behaviour.
      → done when: `grep -l "_tmp_db" tests/test_*.py` is empty and `tmp_db` is in conftest.py
- [ ] `app_client` (auth.db + platform.db patch + `TestClient(app)`, no startup handlers) —
      migrate the 9 clones; a param/second fixture covers the `resolve_base_path` variant.
      → done when: `grep -l "TestClient(app)" tests/test_*.py` is empty and `app_client` is in conftest.py
- [ ] `make_user` (create + optional grant + token headers) and `charged_game` helpers — migrate
      `_user`/`_game`/`_make_game` call sites.
      → done when: `grep -l "def _user(\|def _game(\|def _make_game(" tests/test_*.py` is empty
- [ ] Move `_Resp` into conftest (or a `tests/fakes.py`) — dedupe `test_gpu_queue`/
      `test_worker_agent`.
      → done when: `grep -l "class _Resp" tests/test_*.py` is empty (currently test_gpu_queue/test_worker_agent)
- [ ] Delete the 36 redundant per-file `sys.path.insert` shims (conftest owns it).
      → done when: `grep -l "sys.path.insert" tests/test_*.py` is empty (currently 35 files match)

### T2 — Rewrite `tests/README.md` truthfully
Files: `tests/README.md`. Verify: every fixture it names exists in `tests/conftest.py`.
- [ ] Keep the conventions + reviewer checklist; replace the dead fixture list with T1's real
      one; fix the count; document `build_harness.run_build_to_completion` and
      `tests/fixtures/` as the shared build-drive / realistic-fixture patterns.
      → done when: `grep -c "582+\|spec_factory\|run_state\|frozen_run" tests/README.md` is 0

### T3 — Framework unification (small)
Files: `tests/test_ws_event_routing.py`, `tests/test_websocket_errors.py`. Verify: no
`unittest.TestCase` import left in the unit suite; suite green.
- [ ] Migrate both to pytest (async via the anyio plugin already installed).
      → done when: `grep -L unittest tests/test_ws_event_routing.py tests/test_websocket_errors.py` lists both

### T4 — Re-audit cadence
- [ ] Each milestone (or ~+150 tests), rerun this recon: count/distribution, clone-grep
      (`grep -c "def _tmp_db\|TestClient(app)\|class _Resp" tests/test_*.py`), `parametrize`
      opportunities, README accuracy. This file's Background is the template; update it in place.
      → done when: pytest collects ≥967 tests (812+150) and Background above is updated in place

## Parked
- Splitting the `test_codegen.py` monolith (70 tests: kit runtime / gates / module checks /
  build steps) — organizational only, no quality defect; revisit if it keeps growing.
- The 6 × ~0.5s workqueue long-poll wall-clock tests — real-time by design (they test the poll
  window); a clock seam is not worth the prod change today.
- The old scroll-gate false positive is a PRODUCT issue (memory: project_scroll_gate_false_positive),
  not a test-health one — do not "fix" it here.
