# Tests — conventions

582+ unit tests, high quality. The threat isn't weak asserts — it's **structural bloat**: adding
coverage by cloning whole files. These conventions keep the suite growing in quality, not count.

Run: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`
(`tests/integration/` needs live services — skip unless testing connectors.)

## Rules

- **Test behaviour and contracts, not implementation.** Assert on observable output, not private
  call sequences or graph node indices. A thin `assert res["ok"] is True` is not a test — assert
  the follow-up state it produced.
- **Every test carries a WHY-comment** tying it to a real bug or invariant. This is the suite's
  best asset; keep it.
- **Reuse `conftest.py` fixtures + helpers — never re-clone them.** Available:
  - Fixtures (inject as params): `spec_factory`, `run_state`, `frozen_run`, `patch_for_run`,
    `example_ir`, `ctx_factory`.
  - Plain helpers (`from conftest import ...`): `make_spec`, `seed_frozen_run`,
    `patch_run_state_for`, `load_example`, `make_ctx`.
  - Realistic IR fixtures live in `docs/examples/*.json` — load via `load_example("vn_crappy")`,
    never re-inline the path.
- **Variants are `parametrize` rows, not new `def test_` functions.** Combat-shape variants,
  module-resolution cases, etc.
- **One framework: pytest.** No new `unittest.TestCase` files.
- **Mark slow/timing tests** (`@pytest.mark.slow`) and prefer a fake/injected clock over
  wall-clock asserts.
- **Add a test only for a behaviour/contract or a fixed bug.** Don't chase coverage %.

## Reviewer checklist

- [ ] New test has a WHY-comment.
- [ ] Reuses `conftest` fixtures/helpers + `docs/examples` (no re-cloned fixture).
- [ ] Variants are `parametrize` rows, not copied functions.
- [ ] Asserts observable behaviour, not implementation details.
- [ ] pytest style; slow/timing tests marked.
