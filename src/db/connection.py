"""Short-lived sqlite connections for the control plane's two databases."""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

from config.settings_manager import settings_manager
from db.schema import AUTH_SCHEMA, PLATFORM_SCHEMA


def platform_path() -> Path:
    return Path(settings_manager.get_settings()["data_dir"]).resolve() / "platform.db"


def auth_path() -> Path:
    return Path(settings_manager.get_settings()["data_dir"]).resolve() / "auth.db"


_INITIALIZED: set = set()
_INIT_LOCK = threading.Lock()


@contextmanager
def connect(path: Path, schema: str, immediate: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    with _INIT_LOCK:
        if str(path) not in _INITIALIZED:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(schema)
            _INITIALIZED.add(str(path))
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def platform_db(immediate: bool = False):
    with connect(platform_path(), PLATFORM_SCHEMA, immediate) as conn:
        yield conn


@contextmanager
def auth_db(immediate: bool = False):
    with connect(auth_path(), AUTH_SCHEMA, immediate) as conn:
        yield conn


def row_dict(row: Optional[sqlite3.Row]) -> Optional[Dict]:
    return dict(row) if row is not None else None
