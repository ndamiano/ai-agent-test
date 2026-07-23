"""Estimated GPU seconds per queue — the admission-control input.

A job's true cost is only known when a worker completes it, but the budget gate has to decide
BEFORE the job runs. So each queue carries a flat estimate: enqueue RESERVES that estimate against
the game's grant, and the measured debit at completion replaces the reservation when the job leaves
the queue. A game can still overdraw (a job that runs longer than its estimate is never refused
mid-flight) — the reservation bounds how far, which is what stops a build enqueueing faster than
it completes from running a grant into the ground unseen.

Tune these against the jobs table's real exec_seconds; they are a hill-climbable policy constant,
not a measurement.
"""

DEFAULT_SECONDS = 60.0

QUEUE_SECONDS = {
    "llm": 30.0,
    "image": 45.0,
    "mesh": 240.0,
}


def estimate_seconds(queue: str) -> float:
    return QUEUE_SECONDS.get(queue, DEFAULT_SECONDS)


def cheapest_seconds() -> float:
    """What a game needs left to afford even one job. Below this, enqueue refuses everything, so
    admitting the run at all would only buy it a thrash against its step cap."""
    return min(QUEUE_SECONDS.values())
