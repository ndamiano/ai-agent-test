import sqlite3
import os
from typing import Optional


def get_db_path():
    return os.environ.get("TASK_DB_PATH", "data/tasks.db")


def configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def init_db(db_path: Optional[str] = None) -> None:
    if db_path is None:
        db_path = get_db_path()

    dir_path = os.path.dirname(db_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        configure_connection(conn)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'planning', 'in_progress', 'completed', 'failed', 'cancelled', 'archived')),
                execution_mode TEXT NOT NULL DEFAULT 'sequential' CHECK(execution_mode IN ('sequential', 'parallel')),
                working_directory TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS subtasks (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'in_progress', 'completed', 'failed')),
                name TEXT,
                description TEXT,
                goal TEXT NOT NULL,
                input_context TEXT,
                output TEXT,
                depends_on TEXT,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS context_store (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                subtask_id TEXT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE,
                FOREIGN KEY (subtask_id) REFERENCES subtasks (id) ON DELETE CASCADE,
                UNIQUE (task_id, key)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                subtask_id TEXT,
                event_type TEXT NOT NULL CHECK(event_type IN ('task_created', 'task_planned', 'subtask_started', 'subtask_completed', 'subtask_failed', 'task_completed', 'task_failed', 'agent_message')),
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE,
                FOREIGN KEY (subtask_id) REFERENCES subtasks (id) ON DELETE CASCADE
            )
        """)

        conn.commit()

        # Migrate existing databases
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN working_directory TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass

        conn.execute("CREATE INDEX IF NOT EXISTS idx_subtasks_task_id ON subtasks(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_context_store_task_id ON context_store(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_context_store_task_id_key ON context_store(task_id, key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id)")
        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {get_db_path()}")
