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
- [ ] **Populate `conftest.py` with shared fixtures** and delete the per-file clones: `spec_factory`
      / `frozen_run` (reinvented in `test_maestro_tools.py:13,25`, `test_games_hitl_router.py:21`),
      `run_state(tmp_path)`, `ctx` (`test_core.py:56`), `example_ir` loader (copy-pasted across
      `test_voice.py:16`, `test_compile_ir.py:13`, `test_ir_pnc.py:16`, `test_ir_assemble.py:74`,
      `test_godot_compile.py:46`), `patch_for_run` (dup'd `test_games_router.py`↔`test_games_hitl_router.py:16`).
- [ ] **Adopt a `parametrize` convention** for case lists (combat-shape variants, module resolution),
      so new variants are rows not functions.
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
- [ ] **A test-conventions doc / section** (in `CLAUDE.md` or `tests/README.md`): behaviour-not-
      implementation; WHY-comment required; reuse `conftest` fixtures + `docs/examples`; `parametrize`
      for variants; mark slow; one framework (pytest). A reviewer checklist so the suite grows in
      quality, not count.
- [ ] **Re-audit cadence** — a periodic map/quality pass (this recon is the template) each milestone,
      so drift and bloat get caught early.

## Ordering
G1 first (it removes the bloat mechanism and makes every later test cheaper). G4 alongside G1 (codify
while building the fixtures). G2/G3 are steady cleanup/fill-in.
