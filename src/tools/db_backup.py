"""Continuous-enough DB backup: snapshot both sqlite files to the bucket, no third-party mover.

SQLite's online-backup API produces a consistent copy under concurrent writers; the copy is
gzipped and PUT through tools/s3.py to a `latest` key (the restore target) and a dated daily key
(history). Worst-case loss is one interval — and for the rows that matter most, far less:
`mark_dirty()` is called on account and money writes, and a dirty database snapshots within the
debounce window instead of waiting for the tick.

The mechanism survives the planned Postgres migration as-is: the snapshot command swaps for
pg_dump and everything else stays. Like the archiver, this is a boundary — an unconfigured
bucket idles with one log line, and a failed upload never touches the app.
"""

from __future__ import annotations

import gzip
import logging
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from tools import s3

logger = logging.getLogger("db_backup")

INTERVAL_SECONDS = 900.0
DIRTY_DEBOUNCE_SECONDS = 60.0
_TICK = 5.0

_dirty_at: float | None = None
_dirty_lock = threading.Lock()


def mark_dirty() -> None:
    """An account or money row changed — snapshot within the debounce window, not the interval."""
    global _dirty_at
    with _dirty_lock:
        if _dirty_at is None:
            _dirty_at = time.time()


def _take_dirty(now: float) -> bool:
    global _dirty_at
    with _dirty_lock:
        if _dirty_at is not None and now - _dirty_at >= DIRTY_DEBOUNCE_SECONDS:
            _dirty_at = None
            return True
    return False


def _db_paths() -> List[Tuple[str, Path]]:
    from db.store import _db_path as platform_path
    from auth.store import _db_path as auth_path
    return [("platform", platform_path()), ("auth", auth_path())]


def snapshot_all(daily: bool = False) -> int:
    """Snapshot every DB that exists to `db/<name>/latest.db.gz` (+ the dated key when `daily`).
    Returns how many uploaded."""
    count = 0
    for name, path in _db_paths():
        if not path.exists():
            continue
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            src = sqlite3.connect(str(path))
            dst = sqlite3.connect(tmp.name)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            data = gzip.compress(Path(tmp.name).read_bytes())
        try:
            s3.put(f"db/{name}/latest.db.gz", data)
            if daily:
                s3.put(f"db/{name}/{datetime.now(timezone.utc):%Y-%m-%d}.db.gz", data)
        except Exception as e:
            logger.warning("db backup %s failed: %s — app untouched", name, e)
            continue
        count += 1
    return count


class DbBackup:
    """The control-plane thread: a snapshot every INTERVAL, sooner when marked dirty, and the
    day's first snapshot also writes the dated key."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = None
        self._last = 0.0
        self._last_daily = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="db-backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        if not s3.configured():
            logger.info("db backup idle: s3 not configured")
            return
        logger.info("db backup started (interval %ds, dirty debounce %ds)",
                    int(INTERVAL_SECONDS), int(DIRTY_DEBOUNCE_SECONDS))
        while True:
            if self._stop.wait(_TICK):
                return
            now = time.time()
            if not (_take_dirty(now) or now - self._last >= INTERVAL_SECONDS):
                continue
            today = f"{datetime.now(timezone.utc):%Y-%m-%d}"
            try:
                n = snapshot_all(daily=today != self._last_daily)
            except Exception:
                logger.exception("db backup tick failed")
                continue
            if n:
                self._last = now
                self._last_daily = today
