import sqlite3
import json
import uuid
import logging
import re
from typing import Dict, List, Optional

from .schema import init_db, get_db_path
from .connection import get_manager, close_connection
from .constants import TASK_STATUSES, SUBTASK_STATUSES
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)


def _parse_subtask_row(row: dict) -> dict:
    row["input_context"] = json.loads(row["input_context"]) if row["input_context"] else None
    row["depends_on"] = json.loads(row["depends_on"]) if row["depends_on"] else []
    return row


class TaskStore:
    """Database interface for task management."""

    def __init__(self):
        init_db(get_db_path())

    # -------------------------------------------------------------------------
    # Tasks
    # -------------------------------------------------------------------------

    def create_task(self, goal: str, execution_mode: str = "sequential", working_directory: Optional[str] = None,
                    parent_task_id: Optional[str] = None) -> Dict:
        task_id, now = str(uuid.uuid4()), get_utc_timestamp()
        working_directory = working_directory or "outputs"
        with get_manager().transaction() as conn:
            conn.execute(
                "INSERT INTO tasks (id, goal, status, execution_mode, working_directory, parent_task_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, goal, "pending", execution_mode, working_directory, parent_task_id, now, now),
            )
        return {"id": task_id, "goal": goal, "status": "pending",
                "execution_mode": execution_mode, "working_directory": working_directory,
                "parent_task_id": parent_task_id, "created_at": now, "updated_at": now}

    def get_task(self, task_id: str) -> Dict:
        conn = get_manager().acquire()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, goal, status, execution_mode, working_directory, parent_task_id, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Task {task_id} not found")
        task = dict(row)
        if task.get("working_directory") is None:
            task["working_directory"] = "outputs"
        return task

    def update_task_status(self, task_id: str, status: str) -> None:
        if status not in TASK_STATUSES:
            raise ValueError(f"Invalid status: {status}. Must be one of {TASK_STATUSES}")
        with get_manager().transaction() as conn:
            conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, get_utc_timestamp(), task_id))

    def list_tasks(self, status: Optional[str] = None, offset: int = 0, limit: int = 100,
                   root_only: bool = True) -> List[Dict]:
        conn = get_manager().acquire()
        conn.row_factory = sqlite3.Row
        query = "SELECT id, goal, status, execution_mode, working_directory, parent_task_id, created_at, updated_at FROM tasks"
        conditions = []
        params: tuple = ()
        if root_only:
            conditions.append("parent_task_id IS NULL")
        if status:
            conditions.append("status=?")
            params = (status,)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params = params + (limit, offset)
        rows = conn.execute(query, params).fetchall()
        tasks = [dict(r) for r in rows]
        for task in tasks:
            if task.get("working_directory") is None:
                task["working_directory"] = "outputs"
        return tasks

    def get_child_tasks(self, parent_task_id: str) -> List[Dict]:
        conn = get_manager().acquire()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, goal, status, execution_mode, working_directory, parent_task_id, created_at, updated_at FROM tasks WHERE parent_task_id=? ORDER BY created_at",
            (parent_task_id,)
        ).fetchall()
        tasks = [dict(r) for r in rows]
        for task in tasks:
            if task.get("working_directory") is None:
                task["working_directory"] = "outputs"
        return tasks

    # -------------------------------------------------------------------------
    # Subtasks
    # -------------------------------------------------------------------------

    def create_subtask(self, task_id: str, agent_id: str, goal: str, position: int,
                       depends_on: Optional[List[str]] = None,
                       input_context: Optional[Dict] = None,
                       name: Optional[str] = None,
                       description: Optional[str] = None) -> Dict:
        from .validators import validate_dependencies
        validate_dependencies(self, task_id, depends_on or [])
        
        subtask_id, now = str(uuid.uuid4()), get_utc_timestamp()
        with get_manager().transaction() as conn:
            conn.execute(
                """INSERT INTO subtasks
                   (id, task_id, agent_id, status, name, description, goal, input_context, depends_on, position, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (subtask_id, task_id, agent_id, "pending", name, description, goal,
                 json.dumps(input_context) if input_context else None,
                 json.dumps(depends_on) if depends_on else None,
                 position, now, now),
            )
        return {"id": subtask_id, "task_id": task_id, "agent_id": agent_id, "status": "pending",
                "name": name, "description": description, "goal": goal, "input_context": input_context,
                "depends_on": depends_on or [], "position": position, "created_at": now, "updated_at": now}

    def get_subtask(self, subtask_id: str) -> Dict:
        conn = get_manager().acquire()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, task_id, agent_id, status, name, description, goal, input_context, output, depends_on, position, created_at, updated_at FROM subtasks WHERE id=?",
            (subtask_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Subtask {subtask_id} not found")
        return _parse_subtask_row(dict(row))

    def get_subtasks_for_task(self, task_id: str) -> List[Dict]:
        conn = get_manager().acquire()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, task_id, agent_id, status, name, description, goal, input_context, output, depends_on, position, created_at, updated_at FROM subtasks WHERE task_id=? ORDER BY position",
            (task_id,)
        ).fetchall()
        return [_parse_subtask_row(dict(r)) for r in rows]

    def update_subtask_status(self, subtask_id: str, status: str) -> None:
        if status not in SUBTASK_STATUSES:
            raise ValueError(f"Invalid status: {status}. Must be one of {SUBTASK_STATUSES}")
        with get_manager().transaction() as conn:
            conn.execute("UPDATE subtasks SET status=?, updated_at=? WHERE id=?", (status, get_utc_timestamp(), subtask_id))

    def reset_subtasks_for_task(self, task_id: str) -> int:
        """Reset all subtasks for a task to 'pending' status in a single UPDATE."""
        with get_manager().transaction() as conn:
            cur = conn.execute(
                "UPDATE subtasks SET status='pending', updated_at=? WHERE task_id=?",
                (get_utc_timestamp(), task_id),
            )
            return cur.rowcount

    def update_subtask_depends_on(self, subtask_id: str, depends_on: List[str]) -> None:
        with get_manager().transaction() as conn:
            conn.execute("UPDATE subtasks SET depends_on=?, updated_at=? WHERE id=?",
                         (json.dumps(depends_on), get_utc_timestamp(), subtask_id))

    def update_subtask_name_description(self, subtask_id: str, name: Optional[str] = None,
                                        description: Optional[str] = None) -> None:
        with get_manager().transaction() as conn:
            conn.execute("UPDATE subtasks SET name=?, description=?, updated_at=? WHERE id=?",
                         (name, description, get_utc_timestamp(), subtask_id))

    def set_subtask_output(self, subtask_id: str, output: str) -> None:
        subtask = self.get_subtask(subtask_id)
        with get_manager().transaction() as conn:
            conn.execute("UPDATE subtasks SET output=?, status='completed', updated_at=? WHERE id=?",
                         (output, get_utc_timestamp(), subtask_id))
        self._store_context(subtask["task_id"], self._context_key(subtask, subtask_id), output, subtask_id)

    def get_ready_subtasks(self, task_id: str) -> List[Dict]:
        conn = get_manager().acquire()
        conn.row_factory = sqlite3.Row
        pending = [_parse_subtask_row(dict(r)) for r in conn.execute(
            "SELECT id, task_id, agent_id, status, name, description, goal, input_context, output, depends_on, position, created_at, updated_at FROM subtasks WHERE task_id=? AND status='pending' ORDER BY position",
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
        row = get_manager().acquire().execute(
            "SELECT value FROM context_store WHERE task_id=? AND key=? ORDER BY created_at DESC LIMIT 1",
            (task_id, key)
        ).fetchone()
        return row[0] if row else None

    def get_all_context(self, task_id: str) -> Dict[str, str]:
        rows = get_manager().acquire().execute(
            "SELECT key, value FROM context_store WHERE task_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
        return dict(rows)

    def get_context_keys(self, task_id: str) -> List[str]:
        rows = get_manager().acquire().execute(
            "SELECT DISTINCT key FROM context_store WHERE task_id=?", (task_id,)
        ).fetchall()
        return [row[0] for row in rows]

    def update_task_goal(self, task_id: str, goal: str) -> None:
        with get_manager().transaction() as conn:
            conn.execute("UPDATE tasks SET goal=?, updated_at=? WHERE id=?", (goal, get_utc_timestamp(), task_id))

    # ── Refinement helpers ────────────────────────────────────────────────────

    _REFINE_KEY = "refine_messages"

    def get_refine_messages(self, task_id: str) -> List[Dict]:
        raw = self.get_context(task_id, self._REFINE_KEY)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    def set_refine_messages(self, task_id: str, messages: List[Dict]) -> None:
        self._store_context(task_id, self._REFINE_KEY, json.dumps(messages), None)

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def log_event(self, task_id: str, event_type: str, message: str, subtask_id: Optional[str] = None) -> None:
        valid = {"task_created", "task_planned", "subtask_started", "subtask_completed",
                 "subtask_failed", "task_completed", "task_failed", "agent_message"}
        if event_type not in valid:
            raise ValueError(f"Invalid event type: {event_type}")
        with get_manager().transaction() as conn:
            conn.execute(
                "INSERT INTO task_events (id, task_id, subtask_id, event_type, message, created_at) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), task_id, subtask_id, event_type, message, get_utc_timestamp()),
            )

    def get_events(self, task_id: str) -> List[Dict]:
        conn = get_manager().acquire()
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
        context_id, now = str(uuid.uuid4()), get_utc_timestamp()
        try:
            with get_manager().transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO context_store (id, task_id, subtask_id, key, value, created_at) VALUES (?,?,?,?,?,?)",
                    (context_id, task_id, subtask_id, key, value, now),
                )
        except Exception as e:
            logger.warning(f"Failed to store context '{key}' for task {task_id}: {e}")


task_store = TaskStore()