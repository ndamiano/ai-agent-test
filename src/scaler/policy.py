"""Pure scaling decisions — no I/O, no clock, everything injected. Unit-testable outright.

Reap first (a dying pod must stop counting before capacity is measured), then at most one
StartPod per tick. The reaper only ever sees pods the autoscaler already filtered to this
queue's `maestro-<queue>-` prefix, so it can never touch a pod it doesn't manage.

A pod is added only when it is GUARANTEED work: the backlog left when it lands, split across
the fleet it joins, is at least `min_jobs_per_pod` jobs. A boot is billed whole, so a pod that
wakes to two jobs charges its whole create to those two — on the llm queue a 240 s boot at
$2.19/hr is $0.15 the jobs did not need. Queue depth alone said nothing about that: an art
burst of forty 13-second jobs looked deep on every tick of a two-minute boot, and five pods
came up for work the first two finished (2026-09-06: 17, 8, 5, 2 and 0 jobs each).

Age is not a reason to add. A pending job older than some bound says the fleet is still
chewing, not that nothing is coming — an override on it bought the zero-job pod of 2026-09-07
(371 s oldest against a 300 s bound, fourteen jobs left and five pods on them) — and the only
real starvation, no capacity at all, is the `effective == 0` arm above it.

The boot a decision is made against is a CONFIGURED constant, never a measurement. A week of
create-to-registered spans is a week of everything that ever wrote a worker row: the rows
written by a registration rather than a create carry one timestamp for both, read as instant
boots, and dragged the average to a fraction of the truth for as long as they sat in the window
(2026-09-07: llm 20 s against a real 182 s, image 29 s against 78 s) — which set the spawn bar
low enough to buy the zero-job pods the rule exists to refuse. A constant cannot rot that way,
and a boot is a fact about the queue's image, not about the week.
"""

from dataclasses import dataclass
from typing import List, Optional, Union

from scaler.stats import QueueStats, WorkerInfo


@dataclass(frozen=True)
class ScalingPolicy:
    max_workers: int
    cooldown_seconds: float
    boot_deadline_seconds: float
    # What a boot costs, flat, and what a job costs when the week holds no measurement.
    boot_seconds: float
    assumed_job_seconds: float
    # Jobs a new pod must be owed before its boot is worth billing.
    min_jobs_per_pod: float


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

    # Scale-up. A booting pod counts as capacity — without that the same backlog buys a pod on
    # every tick of one boot — and the pod cap counts every surviving managed pod.
    starting = [p for p in pods
                if p.id not in known_pod_ids and p.id not in reaped
                and p.age_seconds <= cfg.boot_deadline_seconds]
    effective = len(live) + len(starting)
    surviving_pods = len(listed - reaped)

    add = False
    if effective == 0:
        # Scale-from-zero on ANY pending job, no cooldown: nothing is coming to drain it.
        add = stats.pending > 0
    else:
        job = stats.job_seconds if stats.job_seconds is not None else cfg.assumed_job_seconds
        # What the fleet on hand chews while the new pod boots, and what is left for it to
        # share with them when it arrives.
        left = stats.pending - effective * cfg.boot_seconds / job
        owed = left / (effective + 1)
        if owed >= cfg.min_jobs_per_pod:
            add = seconds_since_last_scale_up >= cfg.cooldown_seconds
    if add and surviving_pods < cfg.max_workers:
        actions.append(StartPod(queue))

    return actions
