import sqlite3
import threading
import atexit
from contextlib import contextmanager
from typing import Iterator

from .schema import get_db_path, configure_connection


class DatabaseManager:
    """Manages per-thread SQLite connections with explicit commit/rollback."""

    def __init__(self) -> None:
        self._local = threading.local()

    def acquire(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(get_db_path())
            configure_connection(conn)
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.acquire()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close_thread(self) -> None:
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            conn.close()
            self._local.conn = None


_manager = DatabaseManager()


def get_manager() -> DatabaseManager:
    return _manager


def close_connection() -> None:
    _manager.close_thread()


atexit.register(close_connection)
