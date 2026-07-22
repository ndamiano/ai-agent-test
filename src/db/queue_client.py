"""Enqueue side of the worker-pull queue.

One helper every producer shares: land a payload as a jobs row, wait for a worker agent to
claim/execute/complete it, hand back the job row. The LLM connector, the image stage and the
mesh stage all go through here, so a GPU-less control plane can drive every backend that lives
on a GPU box.
"""

import logging
import time
from typing import Dict, Optional

from config.settings_manager import settings_manager
from db import store as db_store
from tools.execution_context import get_run_id

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.25


def _settings() -> Dict:
    return settings_manager.get_settings().get("workqueue") or {}


def enabled() -> bool:
    return bool(_settings().get("enabled"))


def run_job(queue: str, payload: Dict, model: Optional[str] = None,
            timeout_seconds: Optional[float] = None) -> Dict:
    """Enqueue one job and wait for it. Returns the job row; a timeout comes back as
    {"status": "failed", "error": ...} so callers have one shape to branch on."""
    timeout = timeout_seconds or float(_settings().get("job_timeout_seconds", 900))
    job_id = db_store.enqueue_job(queue, payload, game_id=get_run_id(), model=model)
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = db_store.get_job(job_id)
        if job and job["status"] in ("done", "failed"):
            return job
        time.sleep(_POLL_INTERVAL)
    logger.error("queue job %s (%s) timed out after %.0fs", job_id, queue, timeout)
    return {"status": "failed", "error":
            f"queue job {job_id} timed out after {timeout:.0f}s "
            f"(no {queue} worker, or the worker is stuck)"}
