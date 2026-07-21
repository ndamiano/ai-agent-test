"""Pure scaling decisions — no I/O, no clock, everything injected. Unit-testable outright.

Reap first (a dying pod must stop counting before capacity is measured), then at most one
StartPod per tick. The reaper only ever sees pods the autoscaler already filtered to this
queue's `maestro-<queue>-` prefix, so it can never touch a pod it doesn't manage.
"""

from dataclasses import dataclass
from typing import List, Optional, Union

from scaler.stats import QueueStats, WorkerInfo


@dataclass(frozen=True)
class ScalingPolicy:
    max_workers: int
    scale_up_depth_per_worker: int
    scale_up_max_age_seconds: float
    cooldown_seconds: float
    boot_deadline_seconds: float


@dataclass(frozen=True)
class PodInfo:
    id: str
    name: str
    age_seconds: float


@dataclass(frozen=True)
class StartPod:
    queue: str


@dataclass(frozen=True)
class TerminatePod:
    pod_id: str
    reason: str
    worker_id: Optional[str] = None


@dataclass(frozen=True)
class MarkWorkerTerminated:
    worker_id: str


Action = Union[StartPod, TerminatePod, MarkWorkerTerminated]


def decide(queue: str, cfg: ScalingPolicy, stats: QueueStats,
           live: List[WorkerInfo], stale: List[WorkerInfo], terminated: List[WorkerInfo],
           pods: List[PodInfo], seconds_since_last_scale_up: float) -> List[Action]:
    actions: List[Action] = []
    listed = {p.id for p in pods}
    reaped = set()

    # Pods behind deregistered workers: the worker decided to die; the in-pod self-terminate is
    # best-effort, this is the billing guarantee. Only listed pods — a terminate that already
    # landed must not re-fire every tick.
    for w in terminated:
        if w.pod_id in listed and w.pod_id not in reaped:
            actions.append(TerminatePod(w.pod_id, "worker deregistered"))
            reaped.add(w.pod_id)

    # Stale workers: presumed dead (crash without deregister). Mark the row either way so it
    # stops surfacing; kill the pod if it still exists.
    for w in stale:
        if w.pod_id in listed and w.pod_id not in reaped:
            # The executor marks the row only after the terminate lands, so a failed API call
            # leaves the worker stale and the reap retries next tick.
            actions.append(TerminatePod(w.pod_id, "worker stale", worker_id=w.id))
            reaped.add(w.pod_id)
        else:
            actions.append(MarkWorkerTerminated(w.id))

    # Pods no worker ever registered from, past the boot deadline: wedged (image pull loop,
    # bad env) — reap rather than count as capacity forever.
    known_pod_ids = {w.pod_id for w in (*live, *stale, *terminated) if w.pod_id}
    for p in pods:
        if p.id not in known_pod_ids and p.id not in reaped \
                and p.age_seconds > cfg.boot_deadline_seconds:
            actions.append(TerminatePod(p.id, "never registered past boot deadline"))
            reaped.add(p.id)

    # Scale-up. A booting pod counts as capacity (the drain guarantee: depth ÷ effective can't
    # add-forever during a long boot), and the pod cap counts every surviving managed pod.
    starting = [p for p in pods
                if p.id not in known_pod_ids and p.id not in reaped
                and p.age_seconds <= cfg.boot_deadline_seconds]
    effective = len(live) + len(starting)
    surviving_pods = len(listed - reaped)

    add = False
    if effective == 0:
        # Scale-from-zero on ANY pending job, no cooldown: nothing is coming to drain it.
        add = stats.pending > 0
    elif stats.pending >= cfg.scale_up_depth_per_worker * effective \
            or (stats.oldest_pending_age_seconds or 0) > cfg.scale_up_max_age_seconds:
        add = seconds_since_last_scale_up >= cfg.cooldown_seconds
    if add and surviving_pods < cfg.max_workers:
        actions.append(StartPod(queue))

    return actions
