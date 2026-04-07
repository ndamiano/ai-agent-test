"""Unit tests for AgentStore class"""

import unittest
import tempfile
import os
import json
from pathlib import Path
from agents.agent_store import AgentStore


class TestAgentStore(unittest.TestCase):
    """Test cases for AgentStore functionality"""

    def setUp(self):
        """Set up test environment with temporary directory"""
        self.temp_dir = tempfile.mkdtemp()
        self.store = AgentStore(store_dir=self.temp_dir)

    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_new_agent(self):
        """Test saving a new agent to the store"""
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }

        self.store.save(agent)

        file_path = Path(self.temp_dir) / "test-agent.json"
        self.assertTrue(file_path.exists())

        with open(file_path, 'r') as f:
            saved_agent = json.load(f)

        self.assertEqual(saved_agent["id"], "test-agent")
        self.assertEqual(saved_agent["name"], "Test Agent")
        self.assertIn("created_at", saved_agent)
        self.assertIn("updated_at", saved_agent)

    def test_save_existing_agent_updates_timestamps(self):
        """Test that saving an existing agent updates timestamps"""
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }

        self.store.save(agent)
        first_updated = agent["updated_at"]

        import time
        time.sleep(0.01)

        agent["name"] = "Updated Test Agent"
        self.store.save(agent)

        file_path = Path(self.temp_dir) / "test-agent.json"
        with open(file_path, 'r') as f:
            saved_agent = json.load(f)

        self.assertNotEqual(saved_agent["updated_at"], first_updated)

    def test_save_missing_required_fields(self):
        """Test that saving agent with missing required fields raises ValueError"""
        agent = {"name": "Test Agent", "description": "A test agent"}
        with self.assertRaises(ValueError):
            self.store.save(agent)

    def test_get_existing_agent(self):
        """Test retrieving an existing agent"""
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }

        self.store.save(agent)
        retrieved = self.store.get("test-agent")

        self.assertEqual(retrieved["id"], "test-agent")
        self.assertEqual(retrieved["name"], "Test Agent")

    def test_get_nonexistent_agent(self):
        """Test that getting a non-existent agent raises KeyError"""
        with self.assertRaises(KeyError):
            self.store.get("nonexistent-agent")

    def test_list_agents(self):
        """Test listing all agents"""
        agent1 = {
            "id": "agent1",
            "name": "Agent 1",
            "description": "First agent",
            "system_prompt": "You are agent 1",
            "tools": ["tool1"]
        }
        agent2 = {
            "id": "agent2",
            "name": "Agent 2",
            "description": "Second agent",
            "system_prompt": "You are agent 2",
            "tools": ["tool2", "tool3"]
        }

        self.store.save(agent1)
        self.store.save(agent2)

        agents = self.store.list()
        self.assertEqual(len(agents), 2)

        agent_ids = [agent["id"] for agent in agents]
        self.assertIn("agent1", agent_ids)
        self.assertIn("agent2", agent_ids)

    def test_list_agents_empty_store(self):
        """Test listing agents when store is empty"""
        agents = self.store.list()
        self.assertEqual(len(agents), 0)

    def test_exists_true(self):
        """Test exists() method returns True for existing agent"""
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }

        self.store.save(agent)
        self.assertTrue(self.store.exists("test-agent"))

    def test_exists_false(self):
        """Test exists() method returns False for non-existent agent"""
        self.assertFalse(self.store.exists("nonexistent-agent"))

    def test_agent_store_directory_creation(self):
        """Test that store directory is created if it doesn't exist"""
        new_store_dir = os.path.join(self.temp_dir, "new_store")
        new_store = AgentStore(store_dir=new_store_dir)

        self.assertTrue(os.path.exists(new_store_dir))

        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }

        new_store.save(agent)
        retrieved = new_store.get("test-agent")
        self.assertEqual(retrieved["id"], "test-agent")


if __name__ == '__main__':
    unittest.main()