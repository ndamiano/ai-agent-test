"""Build queue — serialize game builds on the single GPU.

The box has one GPU with one resident model, so exactly one build runs at a time. A single
daemon worker drains a FIFO queue; extra builds wait with a visible "position N" the client
reads back over GET /games/{run_id} and the live `build_queued` event.

A queued run already has its RunControl registered (at enqueue), so a pause lands even before
its build leaves the queue.
"""

import logging
import threading
from collections import deque
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AlreadyQueued(Exception):
    """This run is already building or waiting in the queue (caller maps to 409)."""


class _Item:
    def __init__(self, run_id: str, user_id: str, auto_pause: bool, build_id: str):
        self.run_id = run_id
        self.user_id = user_id
        self.auto_pause = auto_pause
        self.build_id = build_id


class BuildQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._pending: deque = deque()
        self._queued_ids: set = set()      # run_ids waiting — dedup + fast membership
        self._current: Optional[str] = None
        self._worker: Optional[threading.Thread] = None
        self._running = False

    # ── lifecycle (app lifespan) ──────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._worker = threading.Thread(target=self._drain, daemon=True, name="build-worker")
            self._worker.start()

    def stop(self) -> None:
        with self._not_empty:
            self._running = False
            self._not_empty.notify_all()

    # ── enqueue + status ──────────────────────────────────────────────────────
    def enqueue(self, run_id: str, user_id: str, auto_pause: bool = False) -> int:
        """Register the run's control and queue its build. Returns the queue position
        (0 = builds immediately). Raises AlreadyQueued if it's already building/waiting."""
        from db import store as db_store
        from maestro.run_control import get_or_create

        with self._not_empty:
            if run_id == self._current or run_id in self._queued_ids:
                raise AlreadyQueued(run_id)
            build_id = db_store.create_build(run_id, kind="build")
            # Register control before the run leaves the queue so an immediate pause finds it.
            get_or_create(run_id).set_auto_pause(auto_pause)
            self._pending.append(_Item(run_id, user_id, auto_pause, build_id))
            self._queued_ids.add(run_id)
            position = self._position_locked(run_id)
            self._not_empty.notify()

        self._emit("build_queued", run_id, position=position)
        return position

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id == self._current or run_id in self._queued_ids

    def state_of(self, run_id: str) -> Optional[Dict]:
        """None if this run isn't in the queue; else {status: building|queued [, position]}."""
        with self._lock:
            if run_id == self._current:
                return {"status": "building"}
            if run_id in self._queued_ids:
                return {"status": "queued", "position": self._position_locked(run_id)}
            return None

    def _position_locked(self, run_id: str) -> int:
        """Builds ahead of `run_id` before it starts (caller holds the lock)."""
        ahead = 1 if self._current is not None else 0
        for item in self._pending:
            if item.run_id == run_id:
                return ahead
            ahead += 1
        return ahead

    # ── worker ────────────────────────────────────────────────────────────────
    def _drain(self) -> None:
        from db import store as db_store
        from maestro.codegen import run as codegen_run  # module ref so tests can monkeypatch run_build

        while True:
            with self._not_empty:
                while self._running and not self._pending:
                    self._not_empty.wait(timeout=0.5)
                if not self._running:
                    return
                item = self._pending.popleft()
                self._queued_ids.discard(item.run_id)
                self._current = item.run_id

            # Everyone still waiting just moved up one — re-emit their positions.
            self._emit_positions()

            db_store.build_started(item.build_id)
            try:
                # The run is charged once (durable charge state on the games row, set before
                # enqueue). Pause, container death, and park-for-human are all resumable and do
                # NOT refund — as long as the run can eventually finish, it stays charged.
                # Refunds are a manual admin action only, never automatic here.
                result = codegen_run.run_build(item.run_id)   # removes its own control in finally
                db_store.build_finished(item.build_id, "succeeded" if result.ok else "failed",
                                        steps=result.steps)
            except Exception:
                logger.exception("build failed for %s", item.run_id)
                db_store.build_finished(item.build_id, "failed")
            finally:
                with self._lock:
                    self._current = None

    # ── events ────────────────────────────────────────────────────────────────
    def _emit(self, event_type: str, run_id: str, **fields) -> None:
        from tools.build_events import _emit as emit
        emit(event_type, run_id, **fields)

    def _emit_positions(self) -> None:
        with self._lock:
            snapshot = [(item.run_id, self._position_locked(item.run_id)) for item in self._pending]
        for run_id, position in snapshot:
            self._emit("build_queued", run_id, position=position)


build_queue = BuildQueue()
