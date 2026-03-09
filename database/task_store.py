import sqlite3
import os
import json
import uuid
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Any
from threading import Lock
import threading
import atexit

from .schema import init_db
from connectors.embedding_client import embedding_client
from .vector_store import store_embedding, retrieve

_thread_local = threading.local()
logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    return os.environ.get("TASK_DB_PATH", "data/tasks.db")


def _get_connection() -> sqlite3.Connection:
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
        conn = sqlite3.connect(_get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        _thread_local.conn = conn
    return _thread_local.conn


def close_connection() -> None:
    conn = getattr(_thread_local, 'conn', None)
    if conn is not None:
        conn.close()
        _thread_local.conn = None


atexit.register(close_connection)


def _now() -> str:
    return datetime.utcnow().isoformat()


def _parse_subtask_row(row: dict) -> dict:
    row["input_context"] = json.loads(row["input_context"]) if row["input_context"] else None
    row["depends_on"] = json.loads(row["depends_on"]) if row["depends_on"] else []
    return row


class TaskStore:
    """Singleton database interface for task management."""

    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    init_db(_get_db_path())
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    # -------------------------------------------------------------------------
    # Tasks
    # -------------------------------------------------------------------------

    def create_task(self, goal: str, execution_mode: str = "sequential") -> Dict:
        task_id, now = str(uuid.uuid4()), _now()
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO tasks (id, goal, status, execution_mode, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (task_id, goal, "pending", execution_mode, now, now),
            )
        return {"id": task_id, "goal": goal, "status": "pending",
                "execution_mode": execution_mode, "created_at": now, "updated_at": now}

    def get_task(self, task_id: str) -> Dict:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, goal, status, execution_mode, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Task {task_id} not found")
        return dict(row)

    def update_task_status(self, task_id: str, status: str) -> None:
        valid = {"pending", "planning", "in_progress", "completed", "failed"}
        if status not in valid:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid}")
        with _get_connection() as conn:
            conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, _now(), task_id))

    def list_tasks(self, status: Optional[str] = None) -> List[Dict]:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        query = "SELECT id, goal, status, execution_mode, created_at, updated_at FROM tasks"
        rows = conn.execute(query + (" WHERE status=?" if status else ""),
                            (status,) if status else ()).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Subtasks
    # -------------------------------------------------------------------------

    def create_subtask(self, task_id: str, agent_id: str, goal: str, position: int,
                       depends_on: Optional[List[str]] = None,
                       input_context: Optional[Dict] = None) -> Dict:
        subtask_id, now = str(uuid.uuid4()), _now()
        with _get_connection() as conn:
            conn.execute(
                """INSERT INTO subtasks
                   (id, task_id, agent_id, status, goal, input_context, depends_on, position, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (subtask_id, task_id, agent_id, "pending", goal,
                 json.dumps(input_context) if input_context else None,
                 json.dumps(depends_on) if depends_on else None,
                 position, now, now),
            )
        return {"id": subtask_id, "task_id": task_id, "agent_id": agent_id, "status": "pending",
                "goal": goal, "input_context": input_context, "depends_on": depends_on or [],
                "position": position, "created_at": now, "updated_at": now}

    def get_subtask(self, subtask_id: str) -> Dict:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, task_id, agent_id, status, goal, input_context, output, depends_on, position, created_at, updated_at FROM subtasks WHERE id=?",
            (subtask_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Subtask {subtask_id} not found")
        return _parse_subtask_row(dict(row))

    def get_subtasks_for_task(self, task_id: str) -> List[Dict]:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, task_id, agent_id, status, goal, input_context, output, depends_on, position, created_at, updated_at FROM subtasks WHERE task_id=? ORDER BY position",
            (task_id,)
        ).fetchall()
        return [_parse_subtask_row(dict(r)) for r in rows]

    def update_subtask_status(self, subtask_id: str, status: str) -> None:
        valid = {"pending", "in_progress", "completed", "failed"}
        if status not in valid:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid}")
        with _get_connection() as conn:
            conn.execute("UPDATE subtasks SET status=?, updated_at=? WHERE id=?", (status, _now(), subtask_id))

    def update_subtask_depends_on(self, subtask_id: str, depends_on: List[str]) -> None:
        with _get_connection() as conn:
            conn.execute("UPDATE subtasks SET depends_on=?, updated_at=? WHERE id=?",
                         (json.dumps(depends_on), _now(), subtask_id))

    def set_subtask_output(self, subtask_id: str, output: str) -> None:
        subtask = self.get_subtask(subtask_id)
        with _get_connection() as conn:
            conn.execute("UPDATE subtasks SET output=?, status='completed', updated_at=? WHERE id=?",
                         (output, _now(), subtask_id))
        self._store_context(subtask["task_id"], self._context_key(subtask, subtask_id), output, subtask_id)

    def get_ready_subtasks(self, task_id: str) -> List[Dict]:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        pending = [_parse_subtask_row(dict(r)) for r in conn.execute(
            "SELECT id, task_id, agent_id, status, goal, input_context, output, depends_on, position, created_at, updated_at FROM subtasks WHERE task_id=? AND status='pending' ORDER BY position",
            (task_id,)
        ).fetchall()]

        ready = []
        for subtask in pending:
            deps = subtask.get("depends_on") or []
            if not deps:
                ready.append(subtask)
                continue
            placeholders = ",".join("?" * len(deps))
            completed = conn.execute(
                f"SELECT COUNT(*) FROM subtasks WHERE id IN ({placeholders}) AND status='completed'", deps
            ).fetchone()[0]
            if completed == len(deps):
                ready.append(subtask)
        return ready

    # -------------------------------------------------------------------------
    # Context
    # -------------------------------------------------------------------------

    def write_context(self, task_id: str, key: str, value: str, subtask_id: Optional[str] = None) -> None:
        self._store_context(task_id, key, value, subtask_id)

    def get_context(self, task_id: str, key: str) -> Optional[str]:
        row = _get_connection().execute(
            "SELECT value FROM context_store WHERE task_id=? AND key=? ORDER BY created_at DESC LIMIT 1",
            (task_id, key)
        ).fetchone()
        return row[0] if row else None

    def get_all_context(self, task_id: str) -> Dict[str, str]:
        rows = _get_connection().execute(
            "SELECT key, value FROM context_store WHERE task_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
        return dict(rows)

    def retrieve_context(self, task_id: str, query: str, k: int = 5) -> List[Dict]:
        try:
            return retrieve(task_id, embedding_client.embed(query), k)
        except Exception as e:
            logger.warning(f"Semantic search failed for '{query}': {e}. Falling back to keyword search.")
            rows = _get_connection().execute(
                "SELECT id, key, value FROM context_store WHERE task_id=? AND value LIKE ? ORDER BY LENGTH(value) LIMIT ?",
                (task_id, f"%{query}%", k)
            ).fetchall()
            return [{"context_id": r[0], "distance": 0.0, "key": r[1], "value": r[2]} for r in rows]

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def log_event(self, task_id: str, event_type: str, message: str, subtask_id: Optional[str] = None) -> None:
        valid = {"task_created", "task_planned", "subtask_started", "subtask_completed",
                 "subtask_failed", "task_completed", "task_failed", "agent_message"}
        if event_type not in valid:
            raise ValueError(f"Invalid event type: {event_type}")
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO task_events (id, task_id, subtask_id, event_type, message, created_at) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), task_id, subtask_id, event_type, message, _now()),
            )

    def get_events(self, task_id: str) -> List[Dict]:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT id, task_id, subtask_id, event_type, message, created_at FROM task_events WHERE task_id=? ORDER BY created_at",
            (task_id,)
        ).fetchall()]

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _context_key(self, subtask: dict, subtask_id: str) -> str:
        base = re.sub(r'[^a-zA-Z0-9_]', '_', subtask.get('goal', '').lower())[:40]
        return f"{base}_{subtask_id[:8]}" if base else f"subtask_output_{subtask_id[:8]}"

    def _store_context(self, task_id: str, key: str, value: str, subtask_id: Optional[str]) -> None:
        context_id, now = str(uuid.uuid4()), _now()
        try:
            with _get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO context_store (id, task_id, subtask_id, key, value, created_at) VALUES (?,?,?,?,?,?)",
                    (context_id, task_id, subtask_id, key, value, now),
                )
            try:
                store_embedding(context_id, task_id, embedding_client.embed(value))
            except Exception as e:
                logger.error(f"Failed to embed context '{key}' for task {task_id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to store context '{key}' for task {task_id}: {e}")


task_store = TaskStore()