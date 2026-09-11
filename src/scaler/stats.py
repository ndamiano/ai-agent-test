"""Queue + fleet facts behind a Protocol — the SQS seam.

The ONLY scaler module that imports db.store. A later move to SQS-like infra swaps this source
(ApproximateNumberOfMessages ≈ pending, etc.); policy.py and autoscaler.py never touch storage.
"""

import time
from typing import Dict, List, NamedTuple, Optional, Protocol

from db import store

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
    def record_worker_created(self, pod_id: str, queue: str, gpu_type: Optional[str],
                              usd_per_hour: Optional[float]) -> None: ...
    def record_pod_refusal(self, queue: str, kind: str, attempts: List[Dict],
                           error: str) -> None: ...
    def record_pod_created(self, queue: str) -> None: ...


class SqliteStatsSource:
    def queue_stats(self, queue: str) -> QueueStats:
        s = store.queue_stats(queue)
        since = time.time() - _WEEK
        return QueueStats(s["pending"], store.recent_job_seconds(queue, since))

    def live_workers(self, queue: str, freshness_seconds: float) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.live_workers(queue, freshness_seconds)]

    def stale_workers(self, queue: str, staleness_seconds: float,
                      boot_deadline_seconds: float) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.stale_workers(queue, staleness_seconds, boot_deadline_seconds)]

    def booting_workers(self, queue: str) -> List[BootingInfo]:
        return [BootingInfo(w["pod_id"], w["started_at"]) for w in store.booting_workers(queue)]

    def terminated_workers_with_pods(self, queue: str) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.terminated_workers_with_pods(queue)]

    def mark_worker_terminated(self, worker_id: str) -> None:
        store.set_worker_terminated(worker_id)

    def mark_pod_terminated(self, pod_id: str) -> None:
        store.set_pod_terminated(pod_id)

    def record_worker_created(self, pod_id: str, queue: str, gpu_type: Optional[str],
                              usd_per_hour: Optional[float]) -> None:
        store.worker_created(pod_id, queue, gpu_type, usd_per_hour)

    def record_pod_refusal(self, queue: str, kind: str, attempts: List[Dict],
                           error: str) -> None:
        store.record_pod_refusal(queue, kind, attempts, error)

    def record_pod_created(self, queue: str) -> None:
        store.record_pod_created(queue)
