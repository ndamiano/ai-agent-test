"""Queue + fleet facts behind a Protocol — the SQS seam.

The ONLY scaler module that imports db.store. A later move to SQS-like infra swaps this source
(ApproximateNumberOfMessages ≈ pending, etc.); policy.py and autoscaler.py never touch storage.
"""

from typing import List, NamedTuple, Optional, Protocol


class QueueStats(NamedTuple):
    pending: int
    claimed: int
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


class SqliteStatsSource:
    def queue_stats(self, queue: str) -> QueueStats:
        from db import store
        s = store.queue_stats(queue)
        return QueueStats(s["pending"], s["claimed"], s["oldest_pending_age_seconds"])

    def live_workers(self, queue: str, freshness_seconds: float) -> List[WorkerInfo]:
        from db import store
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.live_workers(queue, freshness_seconds)]

    def stale_workers(self, queue: str, staleness_seconds: float) -> List[WorkerInfo]:
        from db import store
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.stale_workers(queue, staleness_seconds)]

    def terminated_workers_with_pods(self, queue: str) -> List[WorkerInfo]:
        from db import store
        return [WorkerInfo(w["id"], w["pod_id"])
                for w in store.terminated_workers_with_pods(queue)]

    def mark_worker_terminated(self, worker_id: str) -> None:
        from db import store
        store.set_worker_terminated(worker_id)
