"""Job queue housekeeping: requeue stale workers, fail hanging pending jobs, run lost finalizations.

The reaper is a separate daemon from the autoscaler. The autoscaler only runs when runpod is
enabled+api_key is set; the reaper runs all the time to maintain the shared job queue and work
batches, regardless of scaling backend. Since scaler.stats is deliberately the only scaler module
importing db.store, the reaper is isolated in db/ rather than layered under scaler/.
"""

import logging
import os
import threading

from db import store as db_store

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
        requeued = db_store.requeue_lapsed_leases()
        if requeued:
            logger.info("requeued %d lapsed leases", requeued)

        failed = db_store.fail_stale_pending(STALE_PENDING_SECONDS)
        if failed:
            logger.warning("failed %d stale pending job(s)", len(failed))

        from maestro.codegen import asset_chain, build_chain   # module-level would cycle via db.store
        for batch_id in db_store.batches_awaiting_finalize(FINALIZE_GRACE_SECONDS):
            if asset_chain.run_finalize(batch_id):
                logger.warning("finalized batch %s — its live completion was lost", batch_id)

        # A build whose driver died mid-turn has no in-flight llm job and won't advance itself.
        # Re-drive it from the durable cursor; advance's per-run lock makes this a no-op if a live
        # completion is already advancing it.
        for run_id in db_store.stuck_builds(STUCK_BUILD_GRACE_SECONDS):
            logger.warning("re-advancing stuck build %s — its driver was lost mid-turn", run_id)
            build_chain.advance(run_id)
