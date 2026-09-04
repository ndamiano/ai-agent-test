"""Queue + fleet facts behind a Protocol — the SQS seam.

The ONLY scaler module that imports db.store. A later move to SQS-like infra swaps this source
(ApproximateNumberOfMessages ≈ pending, etc.); policy.py and autoscaler.py never touch storage.
"""

from typing import Dict, List, NamedTuple, Optional, Protocol

from db import store


class QueueStats(NamedTuple):
    pending: int
    oldest_pending_age_seconds: Optional[float]


class WorkerInfo(NamedTuple):
    id: str
    pod_id: Optional[str]


class StatsSource(Protocol):
    def queue_stats(self, queue: str) -> QueueStats: ...
    def live_workers(self, queue: str, freshness_seconds: float) -> List[WorkerInfo]: ...
    def stale_workers(self, queue: str, staleness_seconds: float) -> List[WorkerInfo]: ...
    def terminated_workers_with_pods(self, queue: str) -> List[WorkerInfo]: ...
    def mark_worker_terminated(self, worker_id: str) -> None: ...
    def record_pod_refusal(self, queue: str, kind: str, attempts: List[Dict],
                           error: str) -> None: ...
    def record_pod_created(self, queue: str) -> None: ...


class SqliteStatsSource:
    def queue_stats(self, queue: str) -> QueueStats:
        s = store.queue_stats(queue)
        return QueueStats(s["pending"], s["oldest_pending_age_seconds"])

    def live_workers(self, queue: str, freshness_seconds: float) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.live_workers(queue, freshness_seconds)]

    def stale_workers(self, queue: str, staleness_seconds: float) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.stale_workers(queue, staleness_seconds)]

    def terminated_workers_with_pods(self, queue: str) -> List[WorkerInfo]:
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.terminated_workers_with_pods(queue)]

    def mark_worker_terminated(self, worker_id: str) -> None:
        store.set_worker_terminated(worker_id)

    def record_pod_refusal(self, queue: str, kind: str, attempts: List[Dict],
                           error: str) -> None:
        store.record_pod_refusal(queue, kind, attempts, error)

    def record_pod_created(self, queue: str) -> None:
        store.record_pod_created(queue)
