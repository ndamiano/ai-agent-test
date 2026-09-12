"""Job queue housekeeping: lapsed leases, stale pending jobs, lost finalizations."""

import logging
import os
import threading

from db import games, jobs

logger = logging.getLogger("reaper")

TICK_SECONDS = 5.0
STALE_PENDING_SECONDS = float(os.getenv("MAESTRO_STALE_PENDING_SECONDS", "1800"))
FINALIZE_GRACE_SECONDS = 60.0
STUCK_BUILD_GRACE_SECONDS = 60.0


class Reaper:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="reaper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        logger.info("reaper started")
        while True:
            if self._stop.wait(TICK_SECONDS):
                return
            try:
                self.tick()
            except Exception:
                logger.exception("reaper tick failed")

    def tick(self) -> None:
        requeued = jobs.requeue_lapsed_leases()
        if requeued:
            logger.info("requeued %d lapsed leases", requeued)

        failed = jobs.fail_stale_pending(STALE_PENDING_SECONDS)
        if failed:
            logger.warning("failed %d stale pending job(s)", len(failed))

        from maestro.codegen import asset_chain, build_chain
        for batch_id in jobs.batches_awaiting_finalize(FINALIZE_GRACE_SECONDS):
            if asset_chain.run_finalize(batch_id):
                logger.warning("finalized batch %s — its live completion was lost", batch_id)

        for run_id in games.stuck_builds(STUCK_BUILD_GRACE_SECONDS):
            logger.warning("re-advancing stuck build %s — its driver was lost mid-turn", run_id)
            build_chain.advance(run_id)
