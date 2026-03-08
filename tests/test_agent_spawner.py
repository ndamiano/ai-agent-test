"""Tests for AgentSpawner"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from agents.agent_spawner import AgentSpawner
from agents.main_agent import MainAgent
from tools.tool_manager import tool_manager


class TestAgentSpawner(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Clear any existing tools to have a clean state
        self.original_tools = tool_manager.getTools()
        # Clear the global tool manager for clean tests
        tool_manager._tools_registry.clear()
        
        # Register some test tools
        def test_function1() -> str:
            return "result1"
        
        def test_function2() -> str:
            return "result2"
        
        def test_function3() -> str:
            return "result3"
        
        tool_manager.register_tool(
            name="test_tool1",
            description="First test tool",
            parameters={"type": "object", "properties": {}, "required": []},
            fn=test_function1
        )
        
        tool_manager.register_tool(
            name="test_tool2",
            description="Second test tool",
            parameters={"type": "object", "properties": {}, "required": []},
            fn=test_function2
        )
        
        tool_manager.register_tool(
            name="test_tool3",
            description="Third test tool",
            parameters={"type": "object", "properties": {}, "required": []},
            fn=test_function3
        )
    
    def tearDown(self):
        """Clean up after each test method."""
        # Restore original tools
        tool_manager._tools_registry.clear()
        for tool in self.original_tools:
            tool_manager.register_tool(
                name=tool['name'],
                description=tool['description'],
                parameters=tool['parameters'],
                fn=tool['function']
            )
    
    def test_spawn_agent_all_tools(self):
        """Test spawning an agent with all available tools."""
        spawner = AgentSpawner()
        
        with patch.object(MainAgent, 'chat', return_value="sub-agent result") as mock_chat:
            result = spawner.spawn_agent("test task")
            
            # Verify the agent was created and called
            self.assertEqual(result, "sub-agent result")
            mock_chat.assert_called_once_with("test task")
            
            # Verify the sub-agent was configured with all tools
            # We can't directly test the internal state, but we can verify
            # that the spawn was successful
    
    def test_spawn_agent_specific_tools(self):
        """Test spawning an agent with specific tools only."""
        spawner = AgentSpawner()
        
        with patch.object(MainAgent, 'chat', return_value="sub-agent result") as mock_chat:
            result = spawner.spawn_agent("test task", tools="test_tool1,test_tool2")
            
            # Verify the agent was created and called
            self.assertEqual(result, "sub-agent result")
            mock_chat.assert_called_once_with("test task")
    
    def test_spawn_agent_single_tool(self):
        """Test spawning an agent with a single tool."""
        spawner = AgentSpawner()
        
        with patch.object(MainAgent, 'chat', return_value="sub-agent result") as mock_chat:
            result = spawner.spawn_agent("test task", tools="test_tool1")
            
            # Verify the agent was created and called
            self.assertEqual(result, "sub-agent result")
            mock_chat.assert_called_once_with("test task")
    
    def test_spawn_agent_unknown_tool(self):
        """Test spawning an agent with an unknown tool raises an error."""
        spawner = AgentSpawner()
        
        with self.assertRaises(ValueError) as context:
            spawner.spawn_agent("test task", tools="unknown_tool,test_tool1")
        
        self.assertIn("Unknown tool: unknown_tool", str(context.exception))
    
    def test_spawn_agent_empty_tools_string(self):
        """Test spawning an agent with empty tools string."""
        spawner = AgentSpawner()
        
        with patch.object(MainAgent, 'chat', return_value="sub-agent result") as mock_chat:
            result = spawner.spawn_agent("test task", tools="")
            
            # Should work the same as not specifying tools (get all tools)
            self.assertEqual(result, "sub-agent result")
            mock_chat.assert_called_once_with("test task")
    
    def test_spawn_agent_whitespace_tools(self):
        """Test spawning an agent with whitespace in tools string."""
        spawner = AgentSpawner()
        
        with patch.object(MainAgent, 'chat', return_value="sub-agent result") as mock_chat:
            result = spawner.spawn_agent("test task", tools=" test_tool1 , test_tool2 ")
            
            self.assertEqual(result, "sub-agent result")
            mock_chat.assert_called_once_with("test task")
    
    def test_spawn_agent_sub_agent_execution_error(self):
        """Test handling sub-agent execution errors."""
        spawner = AgentSpawner()
        
        with patch.object(MainAgent, 'chat', side_effect=Exception("Sub-agent failed")) as mock_chat:
            result = spawner.spawn_agent("test task")
            
            # Should return error message instead of raising exception
            self.assertIn("Sub-agent execution failed", result)
            self.assertIn("Sub-agent failed", result)
    
    def test_configure_sub_agent_tools_all_tools(self):
        """Test configuring sub-agent with all available tools."""
        spawner = AgentSpawner()
        sub_agent = MainAgent()
        
        # Mock the sub-agent's tools schema to verify it gets set
        spawner._configure_sub_agent_tools(sub_agent, ["test_tool1", "test_tool2", "test_tool3"])
        
        # Verify that the sub-agent has a tools schema
        self.assertIsNotNone(sub_agent._tools_schema)
        self.assertEqual(len(sub_agent._tools_schema), 3)
        
        # Verify the tools are correctly configured
        tool_names = [tool["function"]["name"] for tool in sub_agent._tools_schema]
        self.assertIn("test_tool1", tool_names)
        self.assertIn("test_tool2", tool_names)
        self.assertIn("test_tool3", tool_names)
    
    def test_configure_sub_agent_tools_subset(self):
        """Test configuring sub-agent with a subset of tools."""
        spawner = AgentSpawner()
        sub_agent = MainAgent()
        
        spawner._configure_sub_agent_tools(sub_agent, ["test_tool1", "test_tool2"])
        
        # Verify that the sub-agent has a tools schema with only 2 tools
        self.assertIsNotNone(sub_agent._tools_schema)
        self.assertEqual(len(sub_agent._tools_schema), 2)
        
        # Verify the correct tools are configured
        tool_names = [tool["function"]["name"] for tool in sub_agent._tools_schema]
        self.assertIn("test_tool1", tool_names)
        self.assertIn("test_tool2", tool_names)
        self.assertNotIn("test_tool3", tool_names)
    
    def test_configure_sub_agent_tools_empty_list(self):
        """Test configuring sub-agent with empty tool list."""
        spawner = AgentSpawner()
        sub_agent = MainAgent()
        
        spawner._configure_sub_agent_tools(sub_agent, [])
        
        # Should result in empty tools schema
        self.assertIsNotNone(sub_agent._tools_schema)
        self.assertEqual(len(sub_agent._tools_schema), 0)


if __name__ == '__main__':
    unittest.main()