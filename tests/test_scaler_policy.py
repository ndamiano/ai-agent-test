"""Pure scaling-policy decisions: the reap rules and the scale-up drain guarantee."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scaler.policy import (
    MarkWorkerTerminated,
    PodInfo,
    ScalingPolicy,
    StartPod,
    TerminatePod,
    decide,
)
from scaler.stats import QueueStats, WorkerInfo

CFG = ScalingPolicy(max_workers=2, scale_up_depth_per_worker=10,
                    scale_up_max_age_seconds=300, cooldown_seconds=90,
                    boot_deadline_seconds=900)

IDLE = QueueStats(pending=0, claimed=0, oldest_pending_age_seconds=None)


def _decide(stats=IDLE, live=(), stale=(), terminated=(), pods=(), since=1e9, cfg=CFG):
    return decide("mesh", cfg, stats, list(live), list(stale), list(terminated),
                  list(pods), since)


def test_scale_from_zero_on_a_single_pending_job():
    assert _decide(stats=QueueStats(1, 0, 5.0)) == [StartPod("mesh")]


def test_scale_from_zero_ignores_the_cooldown():
    assert _decide(stats=QueueStats(1, 0, 5.0), since=0.0) == [StartPod("mesh")]


def test_empty_queue_adds_nothing():
    assert _decide() == []


def test_a_booting_pod_counts_as_capacity():
    # 5 pending ÷ 1 starting pod < depth threshold: the add-forever-during-boot case.
    pod = PodInfo("p1", "maestro-mesh-a1", age_seconds=60)
    assert _decide(stats=QueueStats(5, 0, 30.0), pods=[pod]) == []


def test_depth_threshold_adds_one_pod():
    live = [WorkerInfo("w1", "p1")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 600)]
    assert _decide(stats=QueueStats(10, 1, 30.0), live=live, pods=pods) == [StartPod("mesh")]


def test_cooldown_blocks_the_add():
    live = [WorkerInfo("w1", "p1")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 600)]
    assert _decide(stats=QueueStats(10, 1, 30.0), live=live, pods=pods, since=10.0) == []


def test_max_workers_caps_the_fleet():
    live = [WorkerInfo("w1", "p1"), WorkerInfo("w2", "p2")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 600), PodInfo("p2", "maestro-mesh-a2", 600)]
    assert _decide(stats=QueueStats(500, 2, 900.0), live=live, pods=pods) == []


def test_oldest_pending_age_triggers_below_the_depth_threshold():
    # 1 pending job stuck 400s behind a worker busy on a long job — starvation, not depth.
    live = [WorkerInfo("w1", "p1")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 600)]
    assert _decide(stats=QueueStats(1, 1, 400.0), live=live, pods=pods) == [StartPod("mesh")]


def test_at_most_one_start_pod_per_tick():
    actions = _decide(stats=QueueStats(100, 0, 500.0))
    assert actions == [StartPod("mesh")]


def test_wedged_pod_reaped_past_the_boot_deadline_and_capacity_recovers():
    pod = PodInfo("p1", "maestro-mesh-a1", age_seconds=1000)
    actions = _decide(stats=QueueStats(1, 0, 5.0), pods=[pod])
    assert TerminatePod("p1", "never registered past boot deadline") in actions
    # the wedged pod no longer counts as capacity, so scale-from-zero fires
    assert StartPod("mesh") in actions


def test_terminated_workers_listed_pod_is_reaped():
    t = [WorkerInfo("w1", "p1")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 60)]
    assert _decide(terminated=t, pods=pods) == [TerminatePod("p1", "worker deregistered")]


def test_terminated_workers_missing_pod_is_not_re_reaped():
    # pod already gone from list_pods — a terminate must not re-fire every tick
    assert _decide(terminated=[WorkerInfo("w1", "p1")]) == []


def test_stale_workers_pod_is_reaped_and_row_marked_via_the_action():
    s = [WorkerInfo("w1", "p1")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 600)]
    assert _decide(stale=s, pods=pods) == [TerminatePod("p1", "worker stale", worker_id="w1")]


def test_stale_worker_with_no_pod_left_is_just_marked():
    assert _decide(stale=[WorkerInfo("w1", "p1")]) == [MarkWorkerTerminated("w1")]


def test_reaped_stale_pod_frees_capacity_for_scale_from_zero():
    s = [WorkerInfo("w1", "p1")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 600)]
    actions = _decide(stats=QueueStats(3, 0, 20.0), stale=s, pods=pods)
    assert TerminatePod("p1", "worker stale", worker_id="w1") in actions
    assert StartPod("mesh") in actions


def test_reap_respects_the_cap_when_counting_survivors():
    # 2 pods, one being reaped → 1 survivor < max_workers, so an add is allowed
    s = [WorkerInfo("w1", "p1")]
    live = [WorkerInfo("w2", "p2")]
    pods = [PodInfo("p1", "maestro-mesh-a1", 600), PodInfo("p2", "maestro-mesh-a2", 600)]
    actions = _decide(stats=QueueStats(50, 1, 400.0), stale=s, live=live, pods=pods)
    assert StartPod("mesh") in actions
