"""The asset chain: a job carries what happens after it.

The asset stage no longer waits. Every image job is enqueued at once (so queue depth is real work
the scaler can act on), each one carries the mesh job its result feeds, and the completion that
empties the batch owns the finalize. These tests pin the two things that make that safe: the
continuation lands INSIDE the completion transaction and before the batch is counted, and the
finalize is claimed exactly once no matter who gets there first.
"""

import pytest

from db import store


def _game(seconds: float = 10_000.0, game_id: str = "g1") -> str:
    store.create_game(game_id, "u1")
    store.charge_game(game_id, 1, seconds)
    return game_id


def _image_job(batch: str, asset_id: str = "goblin", game_id: str = "g1") -> str:
    return store.enqueue_job(
        "image", {"kind": "comfy_image"}, game_id=game_id, batch_id=batch,
        metadata={"run_id": game_id, "asset_id": asset_id, "kind": "mesh",
                  "then": {"enqueue": "mesh_from_image", "finalize": "assets"}})


_MESH = {"queue": "mesh", "payload": {"kind": "trellis_mesh", "image_b64": "x"},
         "metadata": {"then": {"operations": ["decimate"], "finalize": "assets"}}}


def _complete(job_id: str, worker: str = "w1", continuation=None, error=None):
    store.claim_job(store.get_job(job_id)["queue"], worker, lease_seconds=60)
    return store.complete_job(job_id, worker, {"ok": True}, error, 5.0,
                              continuation=continuation)


def test_a_batch_with_a_continuation_pending_is_not_complete():
    """The load-bearing order: the follow-up is inserted BEFORE the remaining count. Inverted,
    the last image completion sees an empty batch and finalizes work that hasn't been done."""
    _game()
    job = _image_job("b1")
    out = _complete(job, continuation=_MESH)
    assert out["continuation_id"] is not None
    assert out["batch_complete"] is False


def test_the_last_job_of_a_batch_reports_complete():
    _game()
    out = _complete(_image_job("b1"), continuation=None)
    assert out["batch_complete"] is True


def test_only_one_completion_of_a_batch_sees_it_complete():
    _game()
    a, b = _image_job("b1", "one"), _image_job("b1", "two")
    first = _complete(a, worker="w1")
    second = _complete(b, worker="w2")
    assert [first["batch_complete"], second["batch_complete"]] == [False, True]


def test_a_job_outside_any_batch_never_reports_complete():
    _game()
    job = store.enqueue_job("llm", {"n": 1}, game_id="g1")
    out = _complete(job)
    assert out["batch_id"] is None
    assert out["batch_complete"] is False


def test_a_continuation_inherits_its_parent_game_batch_and_build():
    """Nothing enqueues the follow-up inside a run_scope, so the parent row is the only place its
    game can come from — the attribution hole that let the platform's most expensive work run off
    the books."""
    _game()
    parent = store.enqueue_job("image", {"k": 1}, game_id="g1", build_id="bld1", batch_id="b1",
                               metadata={"then": {"finalize": "assets"}})
    out = _complete(parent, continuation=_MESH)
    child = store.get_job(out["continuation_id"])
    assert (child["game_id"], child["build_id"], child["batch_id"]) == ("g1", "bld1", "b1")
    assert child["queue"] == "mesh"


def test_a_continuations_seconds_debit_the_parents_game():
    _game(10_000.0)
    out = _complete(_image_job("b1"), continuation=_MESH)
    before = store.game("g1")["seconds_used"]
    store.claim_job("mesh", "w2", lease_seconds=60)
    store.complete_job(out["continuation_id"], "w2", {"glb_file": "/x.glb"}, None, 30.0)
    assert store.game("g1")["seconds_used"] == pytest.approx(before + 30.0)


def test_a_failed_job_enqueues_no_continuation():
    _game()
    out = _complete(_image_job("b1"), continuation=_MESH, error="comfy died")
    assert out["continuation_id"] is None
    assert out["batch_complete"] is True


def test_a_refused_continuation_still_finishes_the_batch():
    """A budget refusal mid-chain must not strand the batch: the game renders shapes, which is the
    stage's soft-degrade, and the finalize still has to run."""
    _game(store.estimate_seconds("image") + 1.0)
    out = _complete(_image_job("b1"), continuation=_MESH)
    assert out["continuation_id"] is None
    assert out["batch_complete"] is True


def test_a_batch_finalize_is_claimed_exactly_once():
    _game()
    _complete(_image_job("b1"))
    assert store.claim_batch_finalize("b1") is True
    assert store.claim_batch_finalize("b1") is False


def test_batch_jobs_returns_parsed_metadata():
    _game()
    _image_job("b1", "goblin")
    jobs = store.batch_jobs("b1")
    assert [j["metadata"]["asset_id"] for j in jobs] == ["goblin"]


def test_a_game_with_queued_batch_work_reads_as_active():
    _game()
    _image_job("b1")
    assert store.has_active_batch("g1") is True
    _complete(store.batch_jobs("b1")[0]["id"])
    assert store.has_active_batch("g1") is False


def test_unbatched_work_does_not_count_as_an_active_batch():
    _game()
    store.enqueue_job("llm", {"n": 1}, game_id="g1")
    assert store.has_active_batch("g1") is False


def test_a_lapsed_lease_is_requeued_without_any_claim_traffic():
    """claim_job requeues these too, but only when a claim arrives. A queue that goes quiet would
    otherwise hold a dead job and its reservation forever."""
    _game()
    job = store.enqueue_job("mesh", {"k": 1}, game_id="g1")
    store.claim_job("mesh", "w1", lease_seconds=-1)
    assert store.requeue_lapsed_leases() == 1
    assert store.get_job(job)["status"] == "pending"


def test_stale_pending_jobs_fail_and_release_their_reservation():
    _game(10_000.0)
    store.enqueue_job("mesh", {"k": 1}, game_id="g1")
    reserved = store.compute_remaining("g1")
    failed = store.fail_stale_pending(-1.0)
    assert len(failed) == 1
    assert store.compute_remaining("g1") > reserved


def test_a_stranded_batch_is_offered_for_finalize_after_the_grace_window():
    _game()
    _complete(_image_job("b1"))
    assert store.batches_awaiting_finalize(3600.0) == []
    assert store.batches_awaiting_finalize(-1.0) == ["b1"]


def test_a_finalized_batch_is_never_offered_again():
    _game()
    _complete(_image_job("b1"))
    store.claim_batch_finalize("b1")
    assert store.batches_awaiting_finalize(-1.0) == []


def test_a_batch_with_work_left_is_never_offered():
    _game()
    _image_job("b1", "one")
    _image_job("b1", "two")
    _complete(store.batch_jobs("b1")[0]["id"])
    assert store.batches_awaiting_finalize(-1.0) == []


@pytest.fixture
def _asset_env(monkeypatch, tmp_path):
    from maestro.codegen import asset_chain

    staged, events = [], []
    monkeypatch.setattr(asset_chain, "stage_for_play", lambda rd, rid: staged.append(rid))
    monkeypatch.setattr(asset_chain, "_emit",
                        lambda et, rid, **f: events.append((et, f)))
    monkeypatch.setattr(asset_chain, "RunState",
                        lambda rid: type("S", (), {"run_dir": tmp_path})())
    return asset_chain, staged, events


def _md():
    return {"run_id": "g1", "then": {"finalize": "assets"}}


def test_early_finalize_mid_build_defers_staging(_asset_env):
    """A batch lands while the build is still running: renders are on disk, but staging is the
    build finalize's job — and the outcome is a success, not a failure."""
    asset_chain, staged, events = _asset_env
    _game()
    store.set_status("g1", "building")
    asset_chain._finalize_assets(_md(), [{"metadata": {}, "build_id": None}])
    assert staged == []
    assert events == [("assets_done", {"build_id": None, "ok": True, "rendered": []})]


def test_finalize_after_a_built_game_stages(_asset_env):
    """The batch outlives the build: the game is BUILT by the time the last render lands, so the
    finalize must stage, or the art never reaches /play."""
    asset_chain, staged, events = _asset_env
    _game()
    store.set_status("g1", "built")
    asset_chain._finalize_assets(_md(), [{"metadata": {}, "build_id": None}])
    assert staged == ["g1"]
    assert events[0][1]["ok"] is True


def test_finalize_on_a_failed_build_neither_stages_nor_claims_ok(_asset_env):
    asset_chain, staged, events = _asset_env
    _game()
    store.set_status("g1", "failed")
    asset_chain._finalize_assets(_md(), [{"metadata": {}, "build_id": None}])
    assert staged == []
    assert events[0][1]["ok"] is False
