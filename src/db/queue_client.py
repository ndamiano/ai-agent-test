"""Enqueue side of the worker-pull queue.

One helper every producer shares: land a payload as a jobs row, wait for a worker agent to
claim/execute/complete it, hand back the job row. The LLM connector, the image stage and the
mesh stage all go through here — it is the ONLY transport to a GPU, with no direct-call fallback,
which is what makes it a place to meter and to enforce a budget rather than merely a fast path.
"""

import logging
import time
from typing import Dict, Optional

from config.settings_manager import settings_manager
from db import store as db_store
from tools.execution_context import get_build_id, get_run_id

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.25


def _settings() -> Dict:
    return settings_manager.get_settings().get("workqueue") or {}


def run_job(queue: str, payload: Dict, model: Optional[str] = None,
            timeout_seconds: Optional[float] = None) -> Dict:
    """Enqueue one job and wait for it. Returns the job row; a refused budget or a timeout comes
    back as {"status": "failed", "error": ...} so callers have one shape to branch on.

    Enqueue is where the compute budget is enforced: it is the one chokepoint every producer
    (LLM, image, mesh) shares, so a game out of seconds cannot start GPU work from any path."""
    timeout = timeout_seconds or float(_settings().get("job_timeout_seconds", 900))
    try:
        job_id = db_store.enqueue_job(queue, payload, game_id=get_run_id(), build_id=get_build_id(),
                                      model=model)
    except db_store.InsufficientCompute as e:
        logger.error("queue job (%s) refused: %s", queue, e)
        return {"status": "failed", "error": f"compute budget exhausted: {e}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = db_store.get_job(job_id)
        if job and job["status"] in ("done", "failed"):
            db_store.elide_job_result(job_id)
            return job
        time.sleep(_POLL_INTERVAL)
    logger.error("queue job %s (%s) timed out after %.0fs", job_id, queue, timeout)
    error = (f"queue job {job_id} timed out after {timeout:.0f}s "
             f"(no {queue} worker, or the worker is stuck)")
    db_store.abandon_job(job_id, error)
    return {"status": "failed", "error": error}
