"""The admin-view queries over the jobs table: the head of a queue in claim order, what each
worker holds, and the projected backlog."""

import time

from db import store


def _game():
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000)


def test_pending_head_is_claim_order_capped():
    _game()
    ids = [store.enqueue_job("llm", {}, game_id="g1", build_id="b1") for _ in range(4)]
    head = store.pending_jobs_head("llm", 3)
    assert [j["id"] for j in head] == ids[:3]
    assert head[0].keys() == {"id", "game_id", "build_id", "est_seconds", "created_at"}
    assert head[0]["game_id"] == "g1"


def test_claimed_jobs_leave_the_head_and_name_their_worker():
    _game()
    first = store.enqueue_job("llm", {}, game_id="g1", build_id="b1")
    second = store.enqueue_job("llm", {}, game_id="g1", build_id="b1")
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)

    assert [j["id"] for j in store.pending_jobs_head("llm", 10)] == [second]
    held = store.claimed_jobs("llm")
    assert [(j["id"], j["worker_id"]) for j in held] == [(first, "w1")]
    assert held[0]["started_at"] <= time.time()
    assert store.claimed_jobs("image") == []


def test_backlog_seconds_sums_unfinished_estimates():
    _game()
    from db.estimates import QUEUE_SECONDS
    store.enqueue_job("llm", {}, game_id="g1", build_id="b1")
    store.enqueue_job("llm", {}, game_id="g1", build_id="b1")
    assert store.backlog_seconds("llm") == 2 * QUEUE_SECONDS["llm"]

    store.worker_seen("w1", "llm")
    claimed = store.claim_job("llm", "w1", 60)
    store.complete_job(claimed["id"], "w1", {"ok": True}, None, exec_seconds=12.0)
    assert store.backlog_seconds("llm") == QUEUE_SECONDS["llm"]


def _finish(queue, exec_seconds, *, game_id, gpu_type=None, build_id="b1"):
    store.enqueue_job(queue, {}, game_id=game_id, build_id=build_id)
    store.worker_seen("w1", queue)
    claimed = store.claim_job(queue, "w1", 60)
    store.complete_job(claimed["id"], "w1", {"ok": True}, None,
                       exec_seconds=exec_seconds, gpu_type=gpu_type)


def test_exec_seconds_by_gpu_groups_finished_jobs_by_card():
    _game()
    _finish("llm", 10.0, game_id="g1", gpu_type="A")
    _finish("llm", 5.0, game_id="g1", gpu_type="A")
    _finish("mesh", 7.0, game_id="g1", gpu_type="B")
    _finish("llm", 1.0, game_id=None)

    assert store.exec_seconds_by_gpu(0.0) == {"A": 15.0, "B": 7.0, "unknown": 1.0}
    assert store.exec_seconds_by_gpu(time.time() + 10) == {}


def test_games_exec_seconds_is_everything_the_games_ever_ran():
    _game()
    store.create_game("g2", "u1")
    store.charge_game("g2", 1, 10_000)
    _finish("llm", 10.0, game_id="g1", gpu_type="A")
    _finish("image", 4.0, game_id="g1", gpu_type="B")
    _finish("llm", 3.0, game_id="g2", gpu_type="A")
    _finish("llm", 1.0, game_id=None, gpu_type="A")

    assert store.games_exec_seconds_by_gpu(["g1"]) == {"A": 10.0, "B": 4.0}
    assert store.games_exec_seconds_by_gpu(["g1", "g2"]) == {"A": 13.0, "B": 4.0}
    assert store.games_exec_seconds_by_gpu([]) == {}


def test_games_built_since_counts_a_game_once_on_its_full_build():
    _game()
    store.create_game("g2", "u1")
    store.create_game("g3", "u1")
    b1 = store.create_build("g1")
    b2 = store.create_build("g1")
    store.create_build("g2")
    only_change = store.create_build("g3", kind="change")
    store.build_finished(b1, "built")
    store.build_finished(b2, "failed")
    store.build_finished(only_change, "built")

    assert store.games_built_since(0.0) == ["g1"]
    assert store.games_built_since(time.time() + 10) == []


def test_games_change_count_is_the_games_change_rounds_ever():
    _game()
    store.create_game("g2", "u1")
    store.create_build("g1")
    store.create_build("g1", kind="change")
    store.create_build("g1", kind="change")
    store.create_build("g1", kind="fix")
    store.create_build("g2", kind="change")

    assert store.games_change_count(["g1"]) == 2
    assert store.games_change_count(["g1", "g2"]) == 3
    assert store.games_change_count([]) == 0


def test_pod_refusals_count_stock_only_per_window_and_keep_the_latest():
    store.record_pod_refusal("llm", "other", [{"volume": "v", "gpu_type_ids": ["a"],
                                              "error": "401 unauthorized"}], "401 unauthorized")
    store.record_pod_refusal("llm", "stock", [{"volume": "v", "gpu_type_ids": ["a"],
                                              "error": "no instances"}], "no instances")
    store.record_pod_refusal("image", "stock", [], "no instances")
    now = time.time()
    with store._db() as conn:
        conn.execute("UPDATE pod_refusals SET created_at = ? WHERE queue = 'image'",
                     (now - 2 * 24 * 3600,))
    s = store.pod_stockout_stats("llm", now)
    assert (s["last_1h"], s["last_24h"], s["last_7d"]) == (1, 1, 1)
    assert s["last_error"] == "no instances"
    assert abs(s["last_at"] - now) < 5
    assert [r["error"] for r in s["recent"]] == ["no instances"]
    image = store.pod_stockout_stats("image", now)
    assert (image["last_1h"], image["last_24h"], image["last_7d"]) == (0, 0, 1)
    assert image["recent"] == []
    assert store.pod_stockout_stats("mesh", now)["last_at"] is None


def test_pod_refusals_older_than_thirty_days_are_pruned_on_insert():
    store.record_pod_refusal("llm", "stock", [], "old")
    with store._db() as conn:
        conn.execute("UPDATE pod_refusals SET created_at = ?", (time.time() - 31 * 24 * 3600,))
    store.record_pod_refusal("llm", "stock", [], "new")
    with store._db() as conn:
        assert [r["error"] for r in conn.execute("SELECT error FROM pod_refusals")] == ["new"]


def test_pod_request_days_count_every_attempt_and_refusals_by_kind():
    store.record_pod_created("llm")
    store.record_pod_refusal("llm", "stock", [], "no instances")
    store.record_pod_refusal("llm", "other", [], "401")
    store.record_pod_created("image")
    assert store.pod_request_totals("llm", 60) == {
        "attempts": 3, "stock_refusals": 1, "other_refusals": 1,
        "since": time.strftime("%Y-%m-%d", time.gmtime())}
    assert store.pod_request_totals("mesh") == {
        "attempts": 0, "stock_refusals": 0, "other_refusals": 0, "since": None}


def test_pod_request_totals_window_against_all_time():
    store.record_pod_created("llm")
    with store._db() as conn:
        conn.execute("INSERT INTO pod_request_days (queue, day, attempts, stock_refusals) "
                     "VALUES ('llm', '2020-01-01', 10, 4)")
    assert store.pod_request_totals("llm", 60)["attempts"] == 1
    assert store.pod_request_totals("llm") == {
        "attempts": 11, "stock_refusals": 4, "other_refusals": 0, "since": "2020-01-01"}


def test_pod_request_days_survive_the_detail_prune():
    store.record_pod_refusal("llm", "stock", [], "old")
    with store._db() as conn:
        conn.execute("UPDATE pod_refusals SET created_at = ?", (time.time() - 31 * 24 * 3600,))
    store.record_pod_refusal("llm", "stock", [], "new")
    assert store.pod_request_totals("llm")["stock_refusals"] == 2
