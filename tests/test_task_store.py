import unittest
import tempfile
import os
import shutil
from unittest.mock import patch
from database.task_store import TaskStore
from database.schema import DB_PATH as SCHEMA_DB_PATH


class TestTaskStore(unittest.TestCase):
    """Test suite for TaskStore using temporary SQLite database."""
    
    def setUp(self):
        """Set up a fresh temporary database for each test."""
        # Create a temporary directory for the test database
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = os.path.join(self.temp_dir, "test_tasks.db")
        
        # Patch both DB_PATH constants to use our temporary database
        self.db_path_patch = patch.dict('os.environ', {'TASK_DB_PATH': self.temp_db_path})
        self.db_path_patch.start()
        
        # Clear any existing singleton instance to ensure fresh start
        TaskStore._instance = None
        
        # Initialize the database with our temp path
        from database.schema import init_db
        init_db(self.temp_db_path)
    
    def tearDown(self):
        """Clean up temporary database after each test."""
        self.db_path_patch.stop()
        # Remove the temporary directory and all its contents
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Clear singleton instance
        TaskStore._instance = None
    
    def test_singleton_pattern(self):
        """Test that TaskStore follows the singleton pattern."""
        store1 = TaskStore()
        store2 = TaskStore()
        self.assertIs(store1, store2, "TaskStore should return the same instance")
    
    def test_create_and_read_task(self):
        """Test creating a task and reading it back."""
        store = TaskStore()
        
        # Create a task
        goal = "Test task goal"
        execution_mode = "sequential"
        task = store.create_task(goal, execution_mode)
        
        # Verify task was created correctly
        self.assertEqual(task["goal"], goal)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["execution_mode"], execution_mode)
        self.assertIn("id", task)
        self.assertIn("created_at", task)
        self.assertIn("updated_at", task)
        
        # Read the task back
        retrieved_task = store.get_task(task["id"])
        self.assertEqual(retrieved_task["goal"], goal)
        self.assertEqual(retrieved_task["status"], "pending")
        self.assertEqual(retrieved_task["execution_mode"], execution_mode)
    
    def test_update_task_status(self):
        """Test updating task status."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Update status
        store.update_task_status(task["id"], "in_progress")
        
        # Verify status was updated
        updated_task = store.get_task(task["id"])
        self.assertEqual(updated_task["status"], "in_progress")
        self.assertNotEqual(updated_task["updated_at"], task["updated_at"])
    
    def test_create_subtasks_without_dependencies(self):
        """Test creating subtasks without dependencies."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Create subtasks
        subtask1 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent1",
            goal="First subtask",
            position=0
        )
        
        subtask2 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent2",
            goal="Second subtask",
            position=1
        )
        
        # Verify subtasks were created
        self.assertEqual(subtask1["task_id"], task["id"])
        self.assertEqual(subtask1["agent_id"], "agent1")
        self.assertEqual(subtask1["goal"], "First subtask")
        self.assertEqual(subtask1["position"], 0)
        self.assertIsNone(subtask1["depends_on"])
        
        self.assertEqual(subtask2["task_id"], task["id"])
        self.assertEqual(subtask2["agent_id"], "agent2")
        self.assertEqual(subtask2["goal"], "Second subtask")
        self.assertEqual(subtask2["position"], 1)
        self.assertIsNone(subtask2["depends_on"])
    
    def test_create_subtasks_with_dependencies(self):
        """Test creating subtasks with dependencies."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Create subtasks with dependencies
        subtask1 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent1",
            goal="First subtask",
            position=0
        )
        
        subtask2 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent2",
            goal="Second subtask",
            position=1,
            depends_on=[subtask1["id"]]
        )
        
        subtask3 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent3",
            goal="Third subtask",
            position=2,
            depends_on=[subtask1["id"], subtask2["id"]]
        )
        
        # Verify dependencies were set correctly
        self.assertIsNone(subtask1["depends_on"])
        self.assertEqual(subtask2["depends_on"], [subtask1["id"]])
        self.assertEqual(subtask3["depends_on"], [subtask1["id"], subtask2["id"]])
    
    def test_get_ready_subtasks_no_dependencies(self):
        """Test get_ready_subtasks returns subtasks without dependencies."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Create subtasks without dependencies
        subtask1 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent1",
            goal="First subtask",
            position=0
        )
        
        subtask2 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent2",
            goal="Second subtask",
            position=1
        )
        
        # Both subtasks should be ready
        ready_subtasks = store.get_ready_subtasks(task["id"])
        self.assertEqual(len(ready_subtasks), 2)
        ready_ids = [st["id"] for st in ready_subtasks]
        self.assertIn(subtask1["id"], ready_ids)
        self.assertIn(subtask2["id"], ready_ids)
    
    def test_get_ready_subtasks_with_dependencies(self):
        """Test get_ready_subtasks correctly handles dependencies."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Create subtasks with dependencies
        subtask1 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent1",
            goal="First subtask",
            position=0
        )
        
        subtask2 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent2",
            goal="Second subtask",
            position=1,
            depends_on=[subtask1["id"]]
        )
        
        subtask3 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent3",
            goal="Third subtask",
            position=2,
            depends_on=[subtask1["id"]]
        )
        
        # Only subtask1 should be ready initially
        ready_subtasks = store.get_ready_subtasks(task["id"])
        self.assertEqual(len(ready_subtasks), 1)
        self.assertEqual(ready_subtasks[0]["id"], subtask1["id"])
        
        # Complete subtask1
        store.update_subtask_status(subtask1["id"], "completed")
        
        # Now subtask2 and subtask3 should be ready
        ready_subtasks = store.get_ready_subtasks(task["id"])
        self.assertEqual(len(ready_subtasks), 2)
        ready_ids = [st["id"] for st in ready_subtasks]
        self.assertIn(subtask2["id"], ready_ids)
        self.assertIn(subtask3["id"], ready_ids)
    
    def test_get_ready_subtasks_partial_dependencies(self):
        """Test get_ready_subtasks when some dependencies are pending."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Create subtasks with dependencies
        subtask1 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent1",
            goal="First subtask",
            position=0
        )
        
        subtask2 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent2",
            goal="Second subtask",
            position=1
        )
        
        subtask3 = store.create_subtask(
            task_id=task["id"],
            agent_id="agent3",
            goal="Third subtask",
            position=2,
            depends_on=[subtask1["id"], subtask2["id"]]
        )
        
        # Complete only subtask1
        store.update_subtask_status(subtask1["id"], "completed")
        
        # subtask3 should not be ready because subtask2 is still pending
        ready_subtasks = store.get_ready_subtasks(task["id"])
        self.assertEqual(len(ready_subtasks), 1)
        self.assertEqual(ready_subtasks[0]["id"], subtask2["id"])
    
    def test_context_store_write_and_read(self):
        """Test writing and reading context store entries."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Write context entries
        store.write_context(task["id"], "key1", "value1")
        store.write_context(task["id"], "key2", "value2")
        store.write_context(task["id"], "key1", "updated_value1")  # Update existing key
        
        # Read individual context values
        self.assertEqual(store.get_context(task["id"], "key1"), "updated_value1")
        self.assertEqual(store.get_context(task["id"], "key2"), "value2")
        self.assertIsNone(store.get_context(task["id"], "nonexistent_key"))
    
    def test_get_all_context(self):
        """Test get_all_context returns all entries for a task."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Write context entries
        store.write_context(task["id"], "key1", "value1")
        store.write_context(task["id"], "key2", "value2")
        store.write_context(task["id"], "key3", "value3")
        
        # Get all context
        all_context = store.get_all_context(task["id"])
        
        expected = {
            "key1": "value1",
            "key2": "value2", 
            "key3": "value3"
        }
        self.assertEqual(all_context, expected)
    
    def test_event_logging_and_retrieval(self):
        """Test that events are logged and retrieved in order."""
        store = TaskStore()
        
        # Create a task
        task = store.create_task("Test task")
        
        # Log events in sequence
        store.log_event(task["id"], "task_created", "Task created")
        store.log_event(task["id"], "task_planned", "Task planned")
        store.log_event(task["id"], "subtask_started", "Subtask started")
        store.log_event(task["id"], "subtask_completed", "Subtask completed")
        store.log_event(task["id"], "task_completed", "Task completed")
        
        # Retrieve all events
        events = store.get_events(task["id"])
        
        # Verify events are in correct order and contain expected data
        self.assertEqual(len(events), 5)
        
        expected_events = [
            ("task_created", "Task created"),
            ("task_planned", "Task planned"),
            ("subtask_started", "Subtask started"),
            ("subtask_completed", "Subtask completed"),
            ("task_completed", "Task completed")
        ]
        
        for i, (expected_type, expected_message) in enumerate(expected_events):
            self.assertEqual(events[i]["event_type"], expected_type)
            self.assertEqual(events[i]["message"], expected_message)
            self.assertEqual(events[i]["task_id"], task["id"])
    
    def test_execution_mode_from_goal_heuristic(self):
        """Test the execution_mode_from_goal keyword heuristic."""
        from database.execution_config import execution_mode_from_goal
        
        # Test sequential keywords
        sequential_goals = [
            "Write a book",
            "Create a story",
            "Develop a program",
            "Build a website",
            "Compose music",
            "Draft a document"
        ]
        
        for goal in sequential_goals:
            mode = execution_mode_from_goal(goal)
            self.assertEqual(mode, "sequential", f"Goal '{goal}' should be sequential")
        
        # Test parallel keywords
        parallel_goals = [
            "Research topics",
            "Find information",
            "Compare options",
            "Analyze data",
            "Gather resources",
            "Review documents"
        ]
        
        for goal in parallel_goals:
            mode = execution_mode_from_goal(goal)
            self.assertEqual(mode, "parallel", f"Goal '{goal}' should be parallel")
        
        # Test neutral goal (should default to sequential)
        neutral_goals = [
            "Do something",
            "Complete the task",
            "Work on project"
        ]
        
        for goal in neutral_goals:
            mode = execution_mode_from_goal(goal)
            self.assertEqual(mode, "sequential", f"Neutral goal '{goal}' should default to sequential")


if __name__ == "__main__":
    unittest.main()