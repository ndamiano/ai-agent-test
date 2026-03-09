import sqlite3
import os
import json
import uuid
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Any
from threading import Lock

from .schema import init_db
from connectors.embedding_client import embedding_client
from .vector_store import store_embedding


def _get_db_path():
    """Get the current database path from environment variable."""
    return os.environ.get("TASK_DB_PATH", "data/tasks.db")


def _connect():
    """Create a database connection with foreign key constraints enabled."""
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class TaskStore:
    """Singleton database interface for task management operations."""
    
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(TaskStore, cls).__new__(cls)
                    # Initialize database on instantiation with dynamic path
                    init_db(_get_db_path())
        return cls._instance
    
    @classmethod
    def reset(cls):
        """Reset the singleton instance. Useful for testing."""
        with cls._lock:
            cls._instance = None
    
    def create_task(self, goal: str, execution_mode: str = "sequential") -> Dict:
        """Create a new task and return the full task dictionary."""
        task_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                INSERT INTO tasks (id, goal, status, execution_mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (task_id, goal, "pending", execution_mode, now, now))
            conn.commit()
        
        return {
            "id": task_id,
            "goal": goal,
            "status": "pending",
            "execution_mode": execution_mode,
            "created_at": now,
            "updated_at": now
        }
    
    def get_task(self, task_id: str) -> Dict:
        """Get a task by ID, raises KeyError if not found."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, goal, status, execution_mode, created_at, updated_at
                FROM tasks WHERE id = ?
            """, (task_id,))
            row = cursor.fetchone()
            
            if row is None:
                raise KeyError(f"Task with ID {task_id} not found")
            
            return dict(row)
    
    def update_task_status(self, task_id: str, status: str) -> None:
        """Update the status of a task."""
        valid_statuses = ["pending", "planning", "in_progress", "completed", "failed"]
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")
        
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                UPDATE tasks 
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status, datetime.utcnow().isoformat(), task_id))
            conn.commit()
    
    def list_tasks(self, status: Optional[str] = None) -> List[Dict]:
        """List all tasks, optionally filtered by status."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            if status:
                cursor = conn.execute("""
                    SELECT id, goal, status, execution_mode, created_at, updated_at
                    FROM tasks WHERE status = ?
                """, (status,))
            else:
                cursor = conn.execute("""
                    SELECT id, goal, status, execution_mode, created_at, updated_at
                    FROM tasks
                """)
            
            return [dict(row) for row in cursor.fetchall()]
    
    def create_subtask(self, task_id: str, agent_id: str, goal: str, position: int, 
                      depends_on: Optional[List[str]] = None, input_context: Optional[Dict] = None) -> Dict:
        """Create a new subtask and return the full subtask dictionary."""
        subtask_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                INSERT INTO subtasks (id, task_id, agent_id, status, goal, input_context, 
                                    depends_on, position, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                subtask_id, task_id, agent_id, "pending", goal,
                json.dumps(input_context) if input_context else None,
                json.dumps(depends_on) if depends_on else None,
                position, now, now
            ))
            conn.commit()
        
        return {
            "id": subtask_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "status": "pending",
            "goal": goal,
            "input_context": input_context,
            "depends_on": depends_on,
            "position": position,
            "created_at": now,
            "updated_at": now
        }
    
    def get_subtask(self, subtask_id: str) -> Dict:
        """Get a subtask by ID, raises KeyError if not found."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, task_id, agent_id, status, goal, input_context, output, 
                       depends_on, position, created_at, updated_at
                FROM subtasks WHERE id = ?
            """, (subtask_id,))
            row = cursor.fetchone()
            
            if row is None:
                raise KeyError(f"Subtask with ID {subtask_id} not found")
            
            result = dict(row)
            # Parse JSON fields
            if result["input_context"]:
                result["input_context"] = json.loads(result["input_context"])
            if result["depends_on"]:
                result["depends_on"] = json.loads(result["depends_on"])
            
            return result
    
    def get_subtasks_for_task(self, task_id: str) -> List[Dict]:
        """Get all subtasks for a task, ordered by position."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, task_id, agent_id, status, goal, input_context, output, 
                       depends_on, position, created_at, updated_at
                FROM subtasks WHERE task_id = ? ORDER BY position
            """, (task_id,))
            
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                # Parse JSON fields
                if result["input_context"]:
                    result["input_context"] = json.loads(result["input_context"])
                if result["depends_on"]:
                    result["depends_on"] = json.loads(result["depends_on"])
                else:
                    result["depends_on"] = []
                results.append(result)
            
            return results
    
    def update_subtask_status(self, subtask_id: str, status: str) -> None:
        """Update the status of a subtask."""
        valid_statuses = ["pending", "in_progress", "completed", "failed"]
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")
        
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                UPDATE subtasks 
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status, datetime.utcnow().isoformat(), subtask_id))
            conn.commit()
    
    def set_subtask_output(self, subtask_id: str, output: str) -> None:
        """Set the output of a subtask, also updates status to completed and stores context."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                UPDATE subtasks 
                SET output = ?, status = 'completed', updated_at = ?
                WHERE id = ?
            """, (output, datetime.utcnow().isoformat(), subtask_id))
            conn.commit()
        
        # Also store the output as context for semantic search
        # Get the subtask to retrieve task_id
        try:
            subtask = self.get_subtask(subtask_id)
            task_id = subtask['task_id']
            
            # Generate a context key based on the subtask goal
            goal = subtask.get('goal', 'output')
            # Clean up the goal to make a good context key
            context_key = re.sub(r'[^a-zA-Z0-9_]', '_', goal.lower())[:50]
            if not context_key:
                context_key = f"subtask_output_{subtask_id[:8]}"
            
            # Store as context
            self.write_context(task_id, context_key, output, subtask_id=subtask_id)
        except Exception as e:
            # If context storage fails, log but don't fail the main operation
            logging.warning(f"Failed to store context for subtask {subtask_id}: {e}")
    
    def get_ready_subtasks(self, task_id: str) -> List[Dict]:
        """Get subtasks that are ready to run (pending status and all dependencies completed)."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            # Get all pending subtasks for the task
            cursor = conn.execute("""
                SELECT id, task_id, agent_id, status, goal, input_context, output, 
                       depends_on, position, created_at, updated_at
                FROM subtasks WHERE task_id = ? AND status = 'pending' ORDER BY position
            """, (task_id,))
            
            pending_subtasks = []
            for row in cursor.fetchall():
                result = dict(row)
                # Parse JSON fields
                if result["input_context"]:
                    result["input_context"] = json.loads(result["input_context"])
                if result["depends_on"]:
                    result["depends_on"] = json.loads(result["depends_on"])
                pending_subtasks.append(result)
            
            ready_subtasks = []
            for subtask in pending_subtasks:
                depends_on = subtask.get("depends_on", [])
                
                # If no dependencies, it's ready
                if not depends_on:
                    ready_subtasks.append(subtask)
                    continue
                
                # Check if all dependencies are completed
                placeholders = ",".join(["?"] * len(depends_on))
                cursor = conn.execute(f"""
                    SELECT COUNT(*) as completed_count
                    FROM subtasks 
                    WHERE id IN ({placeholders}) AND status = 'completed'
                """, depends_on)
                
                completed_count = cursor.fetchone()["completed_count"]
                
                if completed_count == len(depends_on):
                    ready_subtasks.append(subtask)
            
            return ready_subtasks

    def update_subtask_depends_on(self, subtask_id: str, depends_on: List[str]) -> None:
        """Update the depends_on field of a subtask with real subtask IDs."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                UPDATE subtasks 
                SET depends_on = ?, updated_at = ?
                WHERE id = ?
            """, (json.dumps(depends_on), datetime.utcnow().isoformat(), subtask_id))
            conn.commit()
    
    def write_context(self, task_id: str, key: str, value: str, subtask_id: Optional[str] = None) -> None:
        """Write a context key-value pair for a task and automatically embed the value."""
        context_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                INSERT INTO context_store (id, task_id, subtask_id, key, value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (context_id, task_id, subtask_id, key, value, now))
            conn.commit()
        
        # Try to embed the value for semantic search capability
        try:
            embedding = embedding_client.embed(value)
            store_embedding(context_id, task_id, embedding)
        except Exception as e:
            # Log error but don't corrupt the stored value
            logging.error(f"Failed to create embedding for context '{key}' in task {task_id}: {e}")
            # Don't modify the stored value - it's still usable for exact-key lookups
    
    def get_context(self, task_id: str, key: str) -> Optional[str]:
        """Get a context value by key for a task."""
        with sqlite3.connect(_get_db_path()) as conn:
            cursor = conn.execute("""
                SELECT value FROM context_store 
                WHERE task_id = ? AND key = ? 
                ORDER BY created_at DESC LIMIT 1
            """, (task_id, key))
            
            row = cursor.fetchone()
            if row:
                value = row[0]
                # Strip [EMBEDDING_FAILED] suffix if present
                if value.endswith(' [EMBEDDING_FAILED]'):
                    value = value[:-20]  # Remove the suffix
                return value
            return None
    
    def get_all_context(self, task_id: str) -> Dict[str, str]:
        """Get all context key-value pairs for a task."""
        with sqlite3.connect(_get_db_path()) as conn:
            cursor = conn.execute("""
                SELECT key, value FROM context_store 
                WHERE task_id = ? 
                ORDER BY created_at
            """, (task_id,))
            
            return dict(cursor.fetchall())
    
    def log_event(self, task_id: str, event_type: str, message: str, subtask_id: Optional[str] = None) -> None:
        """Log an event for a task."""
        valid_event_types = ["task_created", "task_planned", "subtask_started", 
                           "subtask_completed", "subtask_failed", "task_completed", 
                           "task_failed", "agent_message"]
        if event_type not in valid_event_types:
            raise ValueError(f"Invalid event type: {event_type}")
        
        event_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        with sqlite3.connect(_get_db_path()) as conn:
            conn.execute("""
                INSERT INTO task_events (id, task_id, subtask_id, event_type, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (event_id, task_id, subtask_id, event_type, message, now))
            conn.commit()
    
    def get_events(self, task_id: str) -> List[Dict]:
        """Get all events for a task, ordered by creation time."""
        with sqlite3.connect(_get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, task_id, subtask_id, event_type, message, created_at
                FROM task_events WHERE task_id = ? ORDER BY created_at
            """, (task_id,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def retrieve_context(self, task_id: str, query: str, k: int = 5) -> List[Dict]:
        """
        Retrieve context entries for a task using semantic search.
        
        Args:
            task_id: The ID of the task to search within
            query: Plain text query to embed and search for
            k: Number of nearest neighbors to return (default: 5)
            
        Returns:
            List of dictionaries with context_id, distance, key, and value fields
        """
        from .vector_store import retrieve
        
        # Try to embed the query text for semantic search
        try:
            query_embedding = embedding_client.embed(query)
            # Retrieve similar context entries
            return retrieve(task_id, query_embedding, k)
        except Exception as e:
            # If embedding fails, fall back to keyword-based search
            import logging
            logging.warning(f"Semantic search failed for query '{query}': {e}. Falling back to keyword search.")
            return self._keyword_search_context(task_id, query, k)
    
    def _keyword_search_context(self, task_id: str, query: str, k: int = 5) -> List[Dict]:
        """
        Fallback keyword-based search when semantic search fails.
        
        Args:
            task_id: The ID of the task to search within
            query: Plain text query to search for
            k: Number of results to return
            
        Returns:
            List of context entries matching the query
        """
        with sqlite3.connect(_get_db_path()) as conn:
            # Use LIKE for keyword search, order by length of value (shorter matches first)
            cursor = conn.execute("""
                SELECT id, key, value
                FROM context_store 
                WHERE task_id = ? AND value LIKE ?
                ORDER BY LENGTH(value)
                LIMIT ?
            """, (task_id, f"%{query}%", k))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'key': row[1], 
                    'value': row[2]
                })
            
            return results


# Global instance
task_store = TaskStore()