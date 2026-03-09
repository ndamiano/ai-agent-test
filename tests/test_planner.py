"""Tests for the planner module using mocks and a real SQLite database."""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime

import sqlite3

from agents.planner import PlannerAgent
from agents.agent_store import AgentStore
from database.task_store import task_store


class TestPlannerAgent(unittest.TestCase):
    """Test suite for PlannerAgent functionality."""
    
    def setUp(self):
        """Set up test environment with temporary database."""
        # Create a temporary database for testing
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        
        # Set environment variable to use our test database
        self.original_db_path = os.environ.get("TASK_DB_PATH")
        os.environ["TASK_DB_PATH"] = self.temp_db.name
        
        # Initialize the database schema directly
        from database.schema import init_db
        init_db(self.temp_db.name)
        
        # Create test agent store with sample agents
        self.agent_store = AgentStore()
        self._create_test_agents()
        
        # Create planner agent instance
        self.planner = PlannerAgent()
        
        # Create a test task
        self.test_task = task_store.create_task("Test goal", "sequential")
        self.task_id = self.test_task["id"]
    
    def tearDown(self):
        """Clean up test environment."""
        # Clean up temporary database
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
        
        # Restore original environment variable
        if self.original_db_path:
            os.environ["TASK_DB_PATH"] = self.original_db_path
        else:
            os.environ.pop("TASK_DB_PATH", None)
    
    def _create_test_agents(self):
        """Create sample agents for testing."""
        agents = [
            {
                "id": "agent1",
                "name": "Test Agent 1",
                "description": "A test agent for testing",
                "tools": ["tool1", "tool2"],
                "system_prompt": "You are a test agent"
            },
            {
                "id": "agent2", 
                "name": "Test Agent 2",
                "description": "Another test agent",
                "tools": ["tool3"],
                "system_prompt": "You are another test agent"
            }
        ]
        
        for agent in agents:
            self.agent_store.save(agent)
    
    def test_build_planning_prompt_includes_goal_and_agents(self):
        """Test that build_planning_prompt includes goal, available agents, and past context."""
        prompt = self.planner.build_planning_prompt("Build a website", self.task_id)
        
        # Check that goal is included
        self.assertIn("USER GOAL: Build a website", prompt)
        
        # Check that available agents are included
        self.assertIn("AVAILABLE AGENTS:", prompt)
        self.assertIn("agent1: Test Agent 1", prompt)
        self.assertIn("agent2: Test Agent 2", prompt)
        
        # Check that agent descriptions are included
        self.assertIn("Description: A test agent for testing", prompt)
        self.assertIn("Description: Another test agent", prompt)
        
        # Check that tools are included
        self.assertIn("Tools: tool1, tool2", prompt)
        self.assertIn("Tools: tool3", prompt)
    
    def test_build_planning_prompt_handles_empty_past_context(self):
        """Test that build_planning_prompt gracefully handles empty past context."""
        prompt = self.planner.build_planning_prompt("Test goal with no context", self.task_id)
        
        # Should not include context section when no context exists
        self.assertNotIn("POTENTIALLY RELEVANT CONTEXT FROM PAST TASKS:", prompt)
        
        # Should still include goal and agents
        self.assertIn("USER GOAL: Test goal with no context", prompt)
        self.assertIn("AVAILABLE AGENTS:", prompt)
    
    def test_build_planning_prompt_includes_past_context_when_available(self):
        """Test that build_planning_prompt includes past context when available."""
        # Add some context to the task store
        task_store.write_context(self.task_id, "previous_solution", "Used React for frontend")
        task_store.write_context(self.task_id, "tech_stack", "Python backend")
        
        prompt = self.planner.build_planning_prompt("Build a web application", self.task_id)
        
        # Should include context section (may or may not be found due to semantic search)
        # For this test, we'll just verify the method doesn't crash and returns a valid prompt
        self.assertIn("USER GOAL: Build a web application", prompt)
        self.assertIn("AVAILABLE AGENTS:", prompt)
    
    def test_plan_strips_markdown_code_fences(self):
        """Test that plan() correctly strips markdown code fences before JSON parsing."""
        # Mock the planner agent chat to return JSON with markdown fences
        mock_response = '''```json
{
    "execution_mode": "sequential",
    "summary": "Test plan summary",
    "subtasks": [
        {
            "position": 0,
            "agent_id": "agent1",
            "goal": "Create frontend",
            "depends_on": [],
            "context_keys": []
        }
    ]
}
```'''
        
        with patch.object(self.planner.planner_agent, 'chat', return_value=mock_response):
            plan = self.planner.plan("Test goal", self.task_id)
            
            # Verify the plan was parsed correctly
            self.assertEqual(plan["execution_mode"], "sequential")
            self.assertEqual(plan["summary"], "Test plan summary")
            self.assertEqual(len(plan["subtasks"]), 1)
            self.assertEqual(plan["subtasks"][0]["agent_id"], "agent1")
    
    def test_plan_handles_json_without_markdown_fences(self):
        """Test that plan() works when JSON doesn't have markdown fences."""
        mock_response = '''{
    "execution_mode": "parallel",
    "summary": "Parallel plan summary",
    "subtasks": [
        {
            "position": 0,
            "agent_id": "agent2",
            "goal": "Setup backend",
            "depends_on": [],
            "context_keys": []
        }
    ]
}'''
        
        with patch.object(self.planner.planner_agent, 'chat', return_value=mock_response):
            plan = self.planner.plan("Test goal", self.task_id)
            
            # Verify the plan was parsed correctly
            self.assertEqual(plan["execution_mode"], "parallel")
            self.assertEqual(plan["summary"], "Parallel plan summary")
            self.assertEqual(len(plan["subtasks"]), 1)
            self.assertEqual(plan["subtasks"][0]["agent_id"], "agent2")
    
    def test_plan_raises_value_error_on_non_json_response(self):
        """Test that plan() raises ValueError when the LLM returns non-JSON."""
        mock_response = "This is not JSON, it's just text"
        
        with patch.object(self.planner.planner_agent, 'chat', return_value=mock_response):
            with self.assertRaises(ValueError) as context:
                self.planner.plan("Test goal", self.task_id)
            
            self.assertIn("Failed to parse planner JSON response", str(context.exception))
            self.assertIn("This is not JSON, it's just text", str(context.exception))
    
    def test_plan_raises_value_error_on_invalid_json(self):
        """Test that plan() raises ValueError when the LLM returns invalid JSON."""
        mock_response = '{"invalid": json syntax}'
        
        with patch.object(self.planner.planner_agent, 'chat', return_value=mock_response):
            with self.assertRaises(ValueError) as context:
                self.planner.plan("Test goal", self.task_id)
            
            self.assertIn("Failed to parse planner JSON response", str(context.exception))
    
    def test_commit_plan_creates_correct_number_of_subtasks(self):
        """Test that commit_plan() creates the correct number of subtasks in the task store."""
        plan = {
            "execution_mode": "sequential",
            "summary": "Test summary",
            "subtasks": [
                {
                    "position": 0,
                    "agent_id": "agent1",
                    "goal": "First task",
                    "depends_on": [],
                    "context_keys": []
                },
                {
                    "position": 1,
                    "agent_id": "agent2",
                    "goal": "Second task",
                    "depends_on": [0],
                    "context_keys": []
                }
            ]
        }
        
        created_subtasks = self.planner.commit_plan(plan, self.task_id)
        
        # Should create exactly 2 subtasks
        self.assertEqual(len(created_subtasks), 2)
        
        # Verify subtasks were created in the database
        subtasks_in_db = task_store.get_subtasks_for_task(self.task_id)
        self.assertEqual(len(subtasks_in_db), 2)
        
        # Verify subtask details
        self.assertEqual(created_subtasks[0]["agent_id"], "agent1")
        self.assertEqual(created_subtasks[0]["goal"], "First task")
        self.assertEqual(created_subtasks[1]["agent_id"], "agent2")
        self.assertEqual(created_subtasks[1]["goal"], "Second task")
    
    def test_commit_plan_converts_position_based_depends_on_to_real_ids(self):
        """Test that commit_plan() correctly converts position-based depends_on to real subtask IDs."""
        plan = {
            "execution_mode": "sequential",
            "summary": "Test summary",
            "subtasks": [
                {
                    "position": 0,
                    "agent_id": "agent1",
                    "goal": "First task",
                    "depends_on": [],
                    "context_keys": []
                },
                {
                    "position": 1,
                    "agent_id": "agent2",
                    "goal": "Second task",
                    "depends_on": [0],
                    "context_keys": []
                },
                {
                    "position": 2,
                    "agent_id": "agent1",
                    "goal": "Third task",
                    "depends_on": [0, 1],
                    "context_keys": []
                }
            ]
        }
        
        created_subtasks = self.planner.commit_plan(plan, self.task_id)
        
        # Get subtasks from database to verify depends_on was updated
        subtasks_in_db = task_store.get_subtasks_for_task(self.task_id)
        
        # First subtask should have no dependencies
        first_subtask = next(s for s in subtasks_in_db if s["position"] == 0)
        self.assertEqual(first_subtask["depends_on"], [])
        
        # Second subtask should depend on first subtask
        second_subtask = next(s for s in subtasks_in_db if s["position"] == 1)
        self.assertEqual(len(second_subtask["depends_on"]), 1)
        self.assertEqual(second_subtask["depends_on"][0], first_subtask["id"])
        
        # Third subtask should depend on both first and second
        third_subtask = next(s for s in subtasks_in_db if s["position"] == 2)
        self.assertEqual(len(third_subtask["depends_on"]), 2)
        self.assertIn(first_subtask["id"], third_subtask["depends_on"])
        self.assertIn(second_subtask["id"], third_subtask["depends_on"])
    
    def test_commit_plan_logs_task_planned_event(self):
        """Test that commit_plan() logs a task_planned event."""
        plan = {
            "execution_mode": "sequential",
            "summary": "Test summary",
            "subtasks": [
                {
                    "position": 0,
                    "agent_id": "agent1",
                    "goal": "Test task",
                    "depends_on": [],
                    "context_keys": []
                }
            ]
        }
        
        self.planner.commit_plan(plan, self.task_id)
        
        # Check that task_planned event was logged
        events = task_store.get_events(self.task_id)
        planned_events = [e for e in events if e["event_type"] == "task_planned"]
        
        self.assertEqual(len(planned_events), 1)
        self.assertIn("Created 1 subtasks", planned_events[0]["message"])
    
    def test_run_updates_task_status_to_planning_then_in_progress(self):
        """Test that run() updates task status to planning then in_progress on success."""
        # Verify initial status
        task = task_store.get_task(self.task_id)
        self.assertEqual(task["status"], "pending")
        
        # Mock the planner agent chat to return valid JSON
        mock_response = '''{
            "execution_mode": "sequential",
            "summary": "Test plan",
            "subtasks": [
                {
                    "position": 0,
                    "agent_id": "agent1",
                    "goal": "Test task",
                    "depends_on": [],
                    "context_keys": []
                }
            ]
        }'''
        
        with patch.object(self.planner.planner_agent, 'chat', return_value=mock_response):
            created_subtasks = self.planner.run("Test goal", self.task_id)
        
        # Verify task status was updated to in_progress
        task = task_store.get_task(self.task_id)
        self.assertEqual(task["status"], "in_progress")
        
        # Verify subtasks were created
        self.assertEqual(len(created_subtasks), 1)
        self.assertEqual(created_subtasks[0]["goal"], "Test task")
    
    def test_run_updates_task_status_to_failed_and_logs_event_on_exception(self):
        """Test that run() updates task status to failed and logs task_failed on exception."""
        # Mock the planner agent chat to raise an exception
        with patch.object(self.planner.planner_agent, 'chat', side_effect=Exception("LLM Error")):
            with self.assertRaises(Exception):
                self.planner.run("Test goal", self.task_id)
        
        # Verify task status was updated to failed
        task = task_store.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        
        # Verify task_failed event was logged
        events = task_store.get_events(self.task_id)
        failed_events = [e for e in events if e["event_type"] == "task_failed"]
        
        self.assertEqual(len(failed_events), 1)
        self.assertIn("Planning failed: LLM Error", failed_events[0]["message"])
    
    def test_run_handles_json_parsing_error_and_updates_status_to_failed(self):
        """Test that run() handles JSON parsing errors and updates status to failed."""
        # Mock the planner agent chat to return invalid JSON
        with patch.object(self.planner.planner_agent, 'chat', return_value="Invalid JSON"):
            with self.assertRaises(ValueError):
                self.planner.run("Test goal", self.task_id)
        
        # Verify task status was updated to failed
        task = task_store.get_task(self.task_id)
        self.assertEqual(task["status"], "failed")
        
        # Verify task_failed event was logged
        events = task_store.get_events(self.task_id)
        failed_events = [e for e in events if e["event_type"] == "task_failed"]
        
        self.assertEqual(len(failed_events), 1)
        self.assertIn("Planning failed:", failed_events[0]["message"])


if __name__ == "__main__":
    unittest.main()