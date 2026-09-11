"""Estimated GPU seconds per queue — the admission-control input — and the price of a job.

A job's true cost is only known when a worker completes it, but the budget gate has to decide
BEFORE the job runs. So each queue carries a flat estimate: enqueue RESERVES that estimate against
the game's grant, and the measured debit at completion replaces the reservation when the job leaves
the queue. A game can still overdraw (a job that runs longer than its estimate is never refused
mid-flight) — the reservation bounds how far, which is what stops a build enqueueing faster than
it completes from running a grant into the ground unseen.

Tune these against the jobs table's real exec_seconds; they are a hill-climbable policy constant,
not a measurement.

Budgets are micros (millionths of a dollar). A job costs its exec_seconds at the hourly rate of
the pod that ran it (`workers.usd_per_hour`, RunPod's own price, stamped at create). The card is
unknown at enqueue, so a reservation is priced at the fallback rate.
"""
import math
from typing import Optional

DEFAULT_SECONDS = 60.0

# A worker with no rate on record (a home box, or a pod the scaler has not priced yet) bills here.
FALLBACK_USD_PER_HOUR = 0.99

# Tuned 2026-09-07 against jobs.exec_seconds since 09-01 (n=1579/203/29): mean 14/17/106,
# p50 5/13/65, p90 34/33/240. Each covers about three quarters of its queue's jobs — a
# reservation is a bound on how far a game can overdraw, not a forecast, and a burst of forty
# art asks at p90 would hold a grant four times what the burst costs.
QUEUE_SECONDS = {
    "llm": 15.0,
    "image": 20.0,
    "mesh": 90.0,
    "video": 120.0,
}


def estimate_seconds(queue: str) -> float:
    return QUEUE_SECONDS.get(queue, DEFAULT_SECONDS)


def job_micros(exec_seconds: float, usd_per_hour: Optional[float]) -> int:
    # round, not ceil, on the rate: 2.09 × 10⁶ is 2089999.9999999998 in floats.
    rate_micros = round((usd_per_hour or FALLBACK_USD_PER_HOUR) * 1_000_000)
    return math.ceil(exec_seconds * rate_micros / 3600)


def reserve_micros(queue: str) -> int:
    return job_micros(estimate_seconds(queue), None)


def cheapest_micros() -> int:
    """What a game needs left to afford even one job. Below this, enqueue refuses everything, so
    admitting the run at all would only buy it a thrash against its step cap."""
    return min(reserve_micros(q) for q in QUEUE_SECONDS)
