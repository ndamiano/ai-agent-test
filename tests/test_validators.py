import unittest
import tempfile
import os
import shutil
from unittest.mock import patch, MagicMock

from database.validators import validate_dependencies, has_circular_dependencies


class TestValidateDependencies(unittest.TestCase):
    """Tests for the shared validators module."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = os.path.join(self.temp_dir, "test_tasks.db")
        self.db_path_patch = patch.dict('os.environ', {'TASK_DB_PATH': self.temp_db_path})
        self.db_path_patch.start()

        from database.task_store import TaskStore
        TaskStore._instance = None
        from database.schema import init_db
        init_db(self.temp_db_path)
        self.store = TaskStore()

    def tearDown(self):
        self.db_path_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        from database.task_store import TaskStore
        TaskStore._instance = None

    def test_empty_depends_on_passes(self):
        """Empty or None dependencies should not raise."""
        task = self.store.create_task("Test task")
        validate_dependencies(self.store, task["id"], None)
        validate_dependencies(self.store, task["id"], [])

    def test_valid_dependency_passes(self):
        """A dependency on an existing subtask should not raise."""
        task = self.store.create_task("Test task")
        subtask = self.store.create_subtask(task["id"], "agent1", "First", position=0)
        validate_dependencies(self.store, task["id"], [subtask["id"]])

    def test_missing_dependency_raises(self):
        """A dependency on a non-existent subtask should raise ValueError."""
        task = self.store.create_task("Test task")
        with self.assertRaises(ValueError) as ctx:
            validate_dependencies(self.store, task["id"], ["nonexistent-id"])
        self.assertIn("nonexistent-id", str(ctx.exception))

    def test_multiple_missing_deps_includes_all(self):
        """Error message should list all missing dependency IDs."""
        task = self.store.create_task("Test task")
        with self.assertRaises(ValueError) as ctx:
            validate_dependencies(self.store, task["id"], ["id-1", "id-2"])
        msg = str(ctx.exception)
        self.assertIn("id-1", msg)
        self.assertIn("id-2", msg)

    def test_valid_chain_dependency_passes(self):
        """A -> B -> C linear chain should not raise."""
        task = self.store.create_task("Test task")
        a = self.store.create_subtask(task["id"], "agent1", "A", position=0)
        b = self.store.create_subtask(task["id"], "agent1", "B", position=1, depends_on=[a["id"]])
        validate_dependencies(self.store, task["id"], [b["id"]])


class TestHasCircularDependencies(unittest.TestCase):
    """Tests for circular dependency detection."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = os.path.join(self.temp_dir, "test_tasks.db")
        self.db_path_patch = patch.dict('os.environ', {'TASK_DB_PATH': self.temp_db_path})
        self.db_path_patch.start()

        from database.task_store import TaskStore
        TaskStore._instance = None
        from database.schema import init_db
        init_db(self.temp_db_path)
        self.store = TaskStore()

    def tearDown(self):
        self.db_path_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        from database.task_store import TaskStore
        TaskStore._instance = None

    def test_no_cycle_on_empty_graph(self):
        """No subtasks means no cycles."""
        task = self.store.create_task("Test task")
        self.assertFalse(has_circular_dependencies(self.store, task["id"], []))

    def test_no_cycle_on_linear_chain(self):
        """A -> B -> C chain has no cycle."""
        task = self.store.create_task("Test task")
        a = self.store.create_subtask(task["id"], "agent1", "A", position=0)
        b = self.store.create_subtask(task["id"], "agent1", "B", position=1, depends_on=[a["id"]])
        self.assertFalse(has_circular_dependencies(self.store, task["id"], [b["id"]]))

    def test_cycle_detected_via_mock(self):
        """Detect existing cycle in graph using a mocked store."""
        mock_store = MagicMock()
        mock_store.get_subtasks_for_task.return_value = [
            {"id": "a", "depends_on": ["b"]},
            {"id": "b", "depends_on": ["c"]},
            {"id": "c", "depends_on": ["a"]},
        ]
        self.assertTrue(has_circular_dependencies(mock_store, "task1", ["a"]))


class TestValidatorIntegration(unittest.TestCase):
    """Integration tests using TaskStore._validate_subtask_dependencies."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = os.path.join(self.temp_dir, "test_tasks.db")
        self.db_path_patch = patch.dict('os.environ', {'TASK_DB_PATH': self.temp_db_path})
        self.db_path_patch.start()

        from database.task_store import TaskStore
        TaskStore._instance = None
        from database.schema import init_db
        init_db(self.temp_db_path)
        self.store = TaskStore()

    def tearDown(self):
        self.db_path_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        from database.task_store import TaskStore
        TaskStore._instance = None

    def test_task_store_rejects_missing_dependency(self):
        """TaskStore.create_subtask should reject dependencies on non-existent subtasks."""
        task = self.store.create_task("Test task")
        with self.assertRaises(ValueError):
            self.store.create_subtask(
                task["id"], "agent1", "Bad subtask", position=0,
                depends_on=["does-not-exist"]
            )

    def test_task_store_accepts_valid_dependency(self):
        """TaskStore.create_subtask should accept valid dependencies."""
        task = self.store.create_task("Test task")
        s1 = self.store.create_subtask(task["id"], "agent1", "First", position=0)
        s2 = self.store.create_subtask(
            task["id"], "agent2", "Second", position=1,
            depends_on=[s1["id"]]
        )
        self.assertEqual(s2["depends_on"], [s1["id"]])


if __name__ == "__main__":
    unittest.main()
