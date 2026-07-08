"""Cross-thread build control — the human-in-the-loop signal channel.

A build runs on a background thread (api.routers.games) while the executor blocks.
The control endpoints run on a different thread, so pause/resume need an in-memory
signal the executor checks at step boundaries. (Human todos and waivers ride on
durable disk state instead — the executor rebuilds context from disk every step, so
it picks those up for free; only pause needs this.)

`status` is the source of truth the API reads back for the run's live state.
"""

import threading
from typing import Dict, Optional


class RunControl:
    def __init__(self):
        self._cond = threading.Condition()
        self._paused = False
        self._auto_pause = False
        self.status = "running"

    @property
    def auto_pause(self) -> bool:
        with self._cond:
            return self._auto_pause

    def set_auto_pause(self, enabled: bool) -> None:
        with self._cond:
            self._auto_pause = enabled

    @property
    def paused(self) -> bool:
        with self._cond:
            return self._paused

    def set_status(self, status: str) -> None:
        with self._cond:
            self.status = status

    def request_pause(self) -> None:
        with self._cond:
            self._paused = True

    def request_resume(self) -> None:
        with self._cond:
            self._paused = False
            self._cond.notify_all()

    def wait_while_paused(self) -> None:
        """Block until resumed. Wakes periodically so a resume that races the pinned
        condition is still observed promptly."""
        with self._cond:
            while self._paused:
                self._cond.wait(timeout=0.5)


# run_id -> RunControl for builds currently in flight.
_REGISTRY: Dict[str, RunControl] = {}
_LOCK = threading.Lock()


def get_or_create(run_id: str) -> RunControl:
    with _LOCK:
        ctrl = _REGISTRY.get(run_id)
        if ctrl is None:
            ctrl = RunControl()
            _REGISTRY[run_id] = ctrl
        return ctrl


def get(run_id: str) -> Optional[RunControl]:
    with _LOCK:
        return _REGISTRY.get(run_id)


def remove(run_id: str) -> None:
    with _LOCK:
        _REGISTRY.pop(run_id, None)
