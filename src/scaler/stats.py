"""Queue + fleet facts behind a Protocol — the SQS seam.
The only scaler module that touches storage; policy.py and autoscaler.py never do.
"""

import time
from typing import Dict, List, NamedTuple, Optional, Protocol

from db import jobs, workers

_WEEK = 7 * 24 * 3600


class QueueStats(NamedTuple):
    pending: int
    # One job's cost to a worker, measured over the last week; None where the week holds none.
    job_seconds: Optional[float] = None


class WorkerInfo(NamedTuple):
    id: str
    pod_id: Optional[str]


class BootingInfo(NamedTuple):
    pod_id: str
    started_at: float


class StatsSource(Protocol):
    def queue_stats(self, queue: str) -> QueueStats: ...
    def live_workers(self, queue: str, freshness_seconds: float) -> List[WorkerInfo]: ...
    def stale_workers(self, queue: str, staleness_seconds: float,
                      boot_deadline_seconds: float) -> List[WorkerInfo]: ...
    def booting_workers(self, queue: str) -> List[BootingInfo]: ...
    def terminated_workers_with_pods(self, queue: str) -> List[WorkerInfo]: ...
    def mark_worker_terminated(self, worker_id: str) -> None: ...
    def mark_pod_terminated(self, pod_id: str) -> None: ...
    def record_worker_created(self, worker_id: str, pod_id: str, queue: str,
                              gpu_type: Optional[str], usd_per_hour: Optional[float],
                              source: str) -> None: ...
    def record_pod_refusal(self, queue: str, kind: str, attempts: List[Dict],
                           error: str) -> None: ...
    def record_pod_created(self, queue: str) -> None: ...


class SqliteStatsSource:
    def queue_stats(self, queue: str) -> QueueStats:
        s = jobs.queue_stats(queue)
        since = time.time() - _WEEK
        return QueueStats(s["pending"], jobs.recent_job_seconds(queue, since))

    def live_workers(self, queue: str, freshness_seconds: float) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in workers.live_workers(queue, freshness_seconds)]

    def stale_workers(self, queue: str, staleness_seconds: float,
                      boot_deadline_seconds: float) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in workers.stale_workers(queue, staleness_seconds, boot_deadline_seconds)]

    def booting_workers(self, queue: str) -> List[BootingInfo]:
        return [BootingInfo(w["pod_id"], w["started_at"]) for w in workers.booting_workers(queue)]

    def terminated_workers_with_pods(self, queue: str) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in workers.terminated_workers_with_pods(queue)]

    def mark_worker_terminated(self, worker_id: str) -> None:
        workers.set_worker_terminated(worker_id)

    def mark_pod_terminated(self, pod_id: str) -> None:
        workers.set_pod_terminated(pod_id)

    def record_worker_created(self, worker_id: str, pod_id: str, queue: str,
                              gpu_type: Optional[str], usd_per_hour: Optional[float],
                              source: str) -> None:
        workers.worker_created(worker_id, pod_id, queue, gpu_type, usd_per_hour, source)

    def record_pod_refusal(self, queue: str, kind: str, attempts: List[Dict],
                           error: str) -> None:
        workers.record_pod_refusal(queue, kind, attempts, error)

    def record_pod_created(self, queue: str) -> None:
        workers.record_pod_created(queue)
