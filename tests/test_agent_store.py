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
        # Remove all files and subdirectories in temp directory
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
        
        # Verify file was created
        file_path = Path(self.temp_dir) / "test-agent.json"
        self.assertTrue(file_path.exists())
        
        # Verify content
        with open(file_path, 'r') as f:
            saved_agent = json.load(f)
        
        self.assertEqual(saved_agent["id"], "test-agent")
        self.assertEqual(saved_agent["name"], "Test Agent")
        self.assertEqual(saved_agent["description"], "A test agent")
        self.assertEqual(saved_agent["system_prompt"], "You are a test agent")
        self.assertEqual(saved_agent["tools"], ["tool1", "tool2"])
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
        
        # Save first time
        self.store.save(agent)
        first_created = agent["created_at"]
        first_updated = agent["updated_at"]
        
        # Wait a moment to ensure different timestamps
        import time
        time.sleep(0.01)
        
        # Save again with modifications
        agent["name"] = "Updated Test Agent"
        self.store.save(agent)
        
        # Verify timestamps were updated
        file_path = Path(self.temp_dir) / "test-agent.json"
        with open(file_path, 'r') as f:
            saved_agent = json.load(f)
        
        self.assertEqual(saved_agent["created_at"], first_created)  # Should remain same
        self.assertNotEqual(saved_agent["updated_at"], first_updated)  # Should be updated
    
    def test_save_missing_required_fields(self):
        """Test that saving agent with missing required fields raises ValueError"""
        # Missing 'id'
        agent = {
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }
        
        with self.assertRaises(ValueError) as context:
            self.store.save(agent)
        self.assertIn("Agent missing required field: id", str(context.exception))
        
        # Missing 'tools'
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent"
        }
        
        with self.assertRaises(ValueError) as context:
            self.store.save(agent)
        self.assertIn("Agent missing required field: tools", str(context.exception))
    
    def test_save_invalid_tools_type(self):
        """Test that saving agent with non-list tools raises ValueError"""
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": "tool1,tool2"  # Should be a list, not string
        }
        
        with self.assertRaises(ValueError) as context:
            self.store.save(agent)
        self.assertIn("Agent tools must be a list", str(context.exception))
    
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
        retrieved_agent = self.store.get("test-agent")
        
        self.assertEqual(retrieved_agent["id"], "test-agent")
        self.assertEqual(retrieved_agent["name"], "Test Agent")
        self.assertEqual(retrieved_agent["description"], "A test agent")
        self.assertEqual(retrieved_agent["system_prompt"], "You are a test agent")
        self.assertEqual(retrieved_agent["tools"], ["tool1", "tool2"])
    
    def test_get_nonexistent_agent(self):
        """Test that getting a non-existent agent raises KeyError"""
        with self.assertRaises(KeyError) as context:
            self.store.get("nonexistent-agent")
        self.assertIn("Agent 'nonexistent-agent' not found", str(context.exception))
    
    def test_list_agents(self):
        """Test listing all agents"""
        # Create multiple agents
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
        
        # Check that both agents are present
        agent_ids = [agent["id"] for agent in agents]
        self.assertIn("agent1", agent_ids)
        self.assertIn("agent2", agent_ids)
    
    def test_list_agents_empty_store(self):
        """Test listing agents when store is empty"""
        agents = self.store.list()
        self.assertEqual(len(agents), 0)
    
    def test_list_agents_with_invalid_json(self):
        """Test that invalid JSON files are skipped when listing agents"""
        # Create a valid agent
        agent = {
            "id": "valid-agent",
            "name": "Valid Agent",
            "description": "A valid agent",
            "system_prompt": "You are a valid agent",
            "tools": ["tool1"]
        }
        self.store.save(agent)
        
        # Create an invalid JSON file
        invalid_file = Path(self.temp_dir) / "invalid-agent.json"
        with open(invalid_file, 'w') as f:
            f.write("invalid json content")
        
        agents = self.store.list()
        
        # Should only return the valid agent
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["id"], "valid-agent")
    
    def test_delete_existing_agent(self):
        """Test deleting an existing agent"""
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }
        
        self.store.save(agent)
        
        # Verify agent exists
        file_path = Path(self.temp_dir) / "test-agent.json"
        self.assertTrue(file_path.exists())
        
        # Delete agent
        self.store.delete("test-agent")
        
        # Verify agent was deleted
        self.assertFalse(file_path.exists())
    
    def test_delete_nonexistent_agent(self):
        """Test that deleting a non-existent agent raises KeyError"""
        with self.assertRaises(KeyError) as context:
            self.store.delete("nonexistent-agent")
        self.assertIn("Agent 'nonexistent-agent' not found", str(context.exception))
    
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
        # Create a new store with a non-existent directory
        new_store_dir = os.path.join(self.temp_dir, "new_store")
        new_store = AgentStore(store_dir=new_store_dir)
        
        # Verify directory was created
        self.assertTrue(os.path.exists(new_store_dir))
        
        # Test saving an agent works
        agent = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent",
            "tools": ["tool1", "tool2"]
        }
        
        new_store.save(agent)
        retrieved_agent = new_store.get("test-agent")
        self.assertEqual(retrieved_agent["id"], "test-agent")


if __name__ == '__main__':
    unittest.main()