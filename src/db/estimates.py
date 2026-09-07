"""Estimated GPU seconds per queue — the admission-control input.

A job's true cost is only known when a worker completes it, but the budget gate has to decide
BEFORE the job runs. So each queue carries a flat estimate: enqueue RESERVES that estimate against
the game's grant, and the measured debit at completion replaces the reservation when the job leaves
the queue. A game can still overdraw (a job that runs longer than its estimate is never refused
mid-flight) — the reservation bounds how far, which is what stops a build enqueueing faster than
it completes from running a grant into the ground unseen.

Tune these against the jobs table's real exec_seconds; they are a hill-climbable policy constant,
not a measurement.

The DEBIT is weighted by the card: a grant is denominated in 5090-seconds, and a second on a
pricier card costs more of them (`billing.gpu_rates`, each card's hourly price over the 5090's).
The estimate is not weighted — the card is unknown at enqueue.
"""
import logging

from config.settings_manager import settings_manager

logger = logging.getLogger(__name__)

DEFAULT_SECONDS = 60.0

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


def cheapest_seconds() -> float:
    """What a game needs left to afford even one job. Below this, enqueue refuses everything, so
    admitting the run at all would only buy it a thrash against its step cap."""
    return min(QUEUE_SECONDS.values())


_unrated: set = set()


def gpu_rate(gpu_type) -> float:
    """5090-seconds debited per second on this card. An unknown card bills at 1.0 and is logged
    once, so a new card under-bills loudly rather than refusing work."""
    rates = settings_manager.get_settings()["billing"]["gpu_rates"]
    if gpu_type in rates:
        return float(rates[gpu_type])
    if gpu_type not in _unrated:
        _unrated.add(gpu_type)
        logger.warning("no billing.gpu_rates entry for gpu_type %r — debiting at 1.0", gpu_type)
    return 1.0
