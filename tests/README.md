# Tests — conventions

~790 unit tests across 77 files, green in under a minute. The threat isn't weak asserts — it's **structural
bloat**: adding coverage by cloning whole files. These conventions keep the suite growing in
quality, not count.

Run: `cd src && python -m pytest ../tests/ -q`

## Rules

- **Test behaviour and contracts, not implementation.** Assert on observable output, not private
  call sequences. A thin `assert res["ok"] is True` is not a test — assert the follow-up state it
  produced.
- **Every test carries a WHY-comment** tying it to a real bug or invariant. This is the suite's
  best asset; keep it.
- **Reuse `conftest.py` — never re-clone it.** What it offers:
  - `isolated_dbs` — **autouse**: both datastores point under the test's `tmp_path`, so no test
    can reach the real platform.db/auth.db. Nothing needs to request it.
  - `app_client` — a `TestClient(app)` with no startup handlers. Inject it as a param.
  - `tmp_runs` — run dirs (`runs/<id>/`) under `tmp_path`, for anything that reads or writes one.
  - `tests/fakes.py` — shared stand-ins, imported rather than injected: `FakeResponse` is what
    `requests` hands back.
- **Variants are `parametrize` rows, not new `def test_` functions.**
- **One framework: pytest.** No new `unittest.TestCase` files.
- **Mark slow/timing tests** (`@pytest.mark.slow`) and prefer a fake/injected clock over
  wall-clock asserts.
- **Add a test only for a behaviour/contract or a fixed bug.** Don't chase coverage %.

## Reviewer checklist

- [ ] New test has a WHY-comment.
- [ ] Reuses the `conftest` fixtures above (no re-cloned db patch, no fresh `TestClient(app)`).
- [ ] Variants are `parametrize` rows, not copied functions.
- [ ] Asserts observable behaviour, not implementation details.
- [ ] pytest style; slow/timing tests marked.
