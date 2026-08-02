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

# Tuned 2026-08-02 against jobs.exec_seconds (n=1801/228/11): p50 3.0/2.3/32.5, p90 12.4/9.0/51.8.
# Each sits near p90 — reservations cover a typical-heavy job without 10x over-holding a game's
# grant during an art burst (image was 45 against a 2.3s median).
QUEUE_SECONDS = {
    "llm": 15.0,
    "image": 8.0,
    "mesh": 90.0,
}


def estimate_seconds(queue: str) -> float:
    return QUEUE_SECONDS.get(queue, DEFAULT_SECONDS)


def cheapest_seconds() -> float:
    """What a game needs left to afford even one job. Below this, enqueue refuses everything, so
    admitting the run at all would only buy it a thrash against its step cap."""
    return min(QUEUE_SECONDS.values())
