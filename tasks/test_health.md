# Test Health — Audit & Anti-Bloat Governance

## Why
582 unit tests (+1 live integration) across 40 files. **Quality is already high** — the suite
overwhelmingly tests behaviour/contracts, avoids over-mocking (~8 call-spy asserts total), has no
golden dumps, and most tests carry a WHY-comment tying them to a real bug/invariant. The threat is
**not weak asserts — it's STRUCTURAL bloat.** The suite has ~1 shared fixture total, so people add
coverage by *cloning whole files*; that's the path from 500 → 10,000 tests. Governance = shared
fixtures + conventions + fixing a small weak fraction + filling load-bearing gaps.

## Guardrails
- Don't chase coverage %. Add a test only for a behaviour/contract or a fixed bug.
- **Consolidate variants with `parametrize`, not new `def test_` functions.**
- Every test keeps a WHY-comment (the suite's best existing asset — codify it).
- Fix implementation-coupled tests toward observable behaviour; don't add more of them.

## Background (VERIFIED — audit findings)
- **Scale:** 582 unit tests / 40 files; `tests/integration/test_connectors.py` (1, live LMStudio) is
  correctly isolated. Run: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`.
- **`conftest.py` is a 4-line `sys.path` shim with ZERO fixtures.** Exactly **one** `@pytest.fixture`
  in the whole suite (`tests/test_vram_management.py:19`). **No `parametrize` anywhere.**
- **Distribution:** maestro core/loop/modules 190 (33%), API routers 93, Ren'Py 79, mechanic
  modules 77, IR 32, assets 35, connector 27, config/utils 26, Godot 23. Heavily tested: module
  core/checks/loop, `build_tools`, combat/world, VN projection. Thin for weight: connector layer,
  main agent, services.
- **Weak fraction ~5–8%**, concentrated (not tautologies):
  - Graph-node-index / hardcoded-string asserts: `tests/test_comfyui_prompt.py:42,55,57` assert on
    ComfyUI workflow node numbers + `ckpt_name`; `:16,38,47` assert prose fragments.
  - Timing-coupled flakes: `tests/test_llm_rate_limit.py:51,66,106` assert wall-clock windows
    (`1.8<=elapsed<=2.5`) — ~3–4s real time, flake-prone on loaded CI.
  - Thin `assert res["ok"] is True` where the real check is the follow-up read
    (`tests/test_maestro_tools.py:42-44`).
  - Private-symbol reach-in is widespread but mostly justified (asserts on output, binds to private
    names) — grey zone, don't expand.
- **Good patterns to preserve:** `monkeypatch RunState.for_run → tmp_path` (router tests exercise
  the real router + RunState, only redirect the FS); `docs/examples/*.json` as shared realistic
  fixtures; the `_ctx`/fake-`State` pattern (`test_core.py:56`); WHY-comment-per-test.

## G1 — Anti-bloat infrastructure (the main lever)
- [x] **Populate `conftest.py` with shared fixtures + helpers** — `spec_factory`/`make_spec`,
      `run_state`, `frozen_run`/`seed_frozen_run`, `patch_for_run`/`patch_run_state_for`,
      `example_ir`/`load_example`, `ctx_factory`/`make_ctx`. Deleted the identical `_patch`/
      `_patch_for_run` clones (routers now import the shared helper); migrated the `example_ir`
      loaders in all 5 IR test files + `_spec`/`_places_spec` in `test_maestro_tools.py`.
      Suite green (691). REMAINING: `_frozen_run` (hitl) + `_ctx` (core) call sites not yet
      threaded onto the fixtures — they're heavier per-call migrations; the fixtures exist so new
      tests use them and the two files can be drained incrementally.
- [ ] **Adopt a `parametrize` convention** for case lists (combat-shape variants, module resolution),
      so new variants are rows not functions. (Convention written into `tests/README.md`; no
      existing case-list converted yet.)
- [ ] **Standardize on pytest-style.** Migrate the 4 `unittest.TestCase` files
      (`test_settings_manager.py`, `test_time_utils.py`, `test_tools.py`, integration) so there's one
      setup idiom to copy.
- [ ] **Add markers + fix the slow file.** Mark `slow`/`timing`; convert `test_llm_rate_limit.py`
      wall-clock asserts to a fake/injected clock (or mark slow) so the default run is fast +
      deterministic.

## G2 — Fix the weak ~5–8%
- [ ] `test_comfyui_prompt.py:42,55,57` — assert the *behaviour* (right checkpoint/params selected),
      not literal graph node indices.
- [ ] `test_llm_rate_limit.py` timing asserts → fake clock (covered by G1).
- [ ] Thin `ok is True`-only tests → assert the observable follow-up state.
- [ ] Tighten brittle `.rpy`/`.gd` substring greps where cheap (judgment call — some are fine for a
      projector).

## G3 — Fill load-bearing coverage gaps (source with NO/thin direct tests)
- [ ] **`src/maestro/services.py` (278 lines) — dedicated `test_services.py`.** The "LIMITS" half of
      the loop contract (budget→`BudgetExhausted`, `dispatch` tool-scope enforcement, `salvage_tool_call`,
      `parse_action`, `_create_guard`) is only piggybacked in `test_core.py:831-847`.
- [ ] **`src/llm_clients/message_builder.py` — NO tests.** `_deduplicate_tool_results`, `_cap_tool_results`,
      `_enforce_budget` — stateful transforms that rot silently.
- [ ] **`src/maestro/context_render.py` (125) — NO tests.** Every build step's prompt context flows
      through it.
- [ ] **`src/maestro/climb.py` (99) — NO tests** (the hill-climb harness).
- [ ] **Projection registry — no direct test** of the `(engine, module_id)` map / `unprojectable`
      fail-fast (`renpy/projections.py`, `godot/projections.py`); only transitively exercised.
- [ ] **`spec_tools` freeze/unfreeze state machine** + `propose_spec`/`amend_spec` — thin (only via
      router). The param/reason resolvers are well covered; the state machine isn't.

## G4 — Governance policy (write it down)
- [x] **A test-conventions doc / section** — `tests/README.md` written: behaviour-not-implementation;
      WHY-comment required; reuse `conftest` fixtures/helpers + `docs/examples`; `parametrize` for
      variants; mark slow; one framework (pytest); + a reviewer checklist.
- [ ] **Re-audit cadence** — a periodic map/quality pass (this recon is the template) each milestone,
      so drift and bloat get caught early.

## G5 — Reduction: test contracts, not "doesn't do X" (net-negative pass)
The suite trends toward too many tests. Prune toward **contracts**, not coverage. A test earns its
place by pinning a promise the code makes; delete tests that pin incidental non-behaviour nobody
promised, or that re-prove one contract N times.
- **KEEP a negative test** only when the refusal IS the contract: validation boundaries (reject
  malformed IR, dupe id, bad shape), security/authz (cross-user 403, off-scope tool refused, frozen
  gate), money (402 when short, charge-once), and any invariant a real bug proved load-bearing (the
  WHY-comment names it). "It refuses X" where X-refusal is the promise = a contract test. Keep.
- **CUT** a test that: (a) asserts an incidental non-behaviour no contract states ("doesn't touch
  unrelated field Y"); (b) re-proves a contract already covered elsewhere (fold into a `parametrize`
  row or delete the dup); (c) pins an implementation detail (node index, literal `.rpy`/`.gd`
  substring, private call order) rather than an observable promise; (d) is a tautology / restates the
  mock.
- [x] **Audit for cut candidates** — DONE (2026-07-08, 55 files). Finding: bloat is STRUCTURAL
      (clone-not-parametrize), not weak-assert. True cuts small (~11); big lever is FOLD (~80 near-dup
      `def test_` fns → ~19 `parametrize`). Headline "doesn't do X" incidental cuts confirmed only in
      `test_comfyui_prompt.py`.
- [~] **Apply approved cuts** — partial (2026-07-08). Applied the high-confidence, contract-preserving
      set (691→686, green):
      - `test_comfyui_prompt`: cut `test_no_pony_booru_quality_tags_leak` (incidental score_* absence);
        trimmed incidental "not in prompt" negatives + `ckpt_name` node-index from `test_item_job` /
        `test_background` / `test_cg` (kept the positive contract in each, renamed).
      - `test_renpy_component_schemas::test_write_node_rejects_start_id` (redundant — `test_maestro_tools`
        covers it).
      - `test_auth_router::test_auth_router_exposes_no_signup_route` (redundant — `test_auth_gate`
        app-level route set is the stronger surface).
      - `test_execution_context::test_nested_contexts` (redundant — `test_nesting_restores_outer_values`
        supersedes: adds subtask_id).
      - `test_llm_rate_limit::test_rate_limiter_maintains_rate` (flaky wall-clock re-proving the bucket
        that `allows_burst`+`refills_tokens` already pin).
      HELD FOR HUMAN: the godot/renpy `.gd`-substring greps (`test_godot_runtime_gd`,
      `test_renpy_fns` overworld, `test_tile_assets` trellis) — technically impl-detail, but the ONLY
      runtime coverage on CI without a godot binary (real self-tests are `skipif`); fix via G3 (drive
      the runtime), don't delete. Plus the UNSURE "doesn't do X" list (compile-tool-absent,
      no-cancel-endpoint, no-signup, foreign-font hygiene) — each may encode a real posture.
- [ ] **Apply the FOLD pass** (~80→~19 parametrized) — the real structural reduction. Biggest files:
      `test_settings_manager` (13→2), `test_ir_crossref` (15→2), `test_world_rpg` (12→2),
      `test_maestro_tools` write_node family (9→2), `test_scenes_prompts`/`test_content_prompts`/
      `test_world_prompts` (~7 each→2). Assertions preserved as rows; net −~60 fns.
- **Guardrail:** the goal is fewer tests that each guard MORE contract, not a coverage-% drop for its
  own sake. Never cut a validation/authz/money refusal. When unsure whether a refusal is a promise,
  KEEP + flag for the human.

## Ordering
G1 first (it removes the bloat mechanism and makes every later test cheaper). G4 alongside G1 (codify
while building the fixtures). G2/G3 are steady cleanup/fill-in. G5 (reduction) is the counterweight
to G3 — run them together so the suite fills real gaps while shedding incidental/redundant tests;
net test count should stay flat or fall, not balloon.
