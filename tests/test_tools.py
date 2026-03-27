"""Tests for ToolManager"""

import unittest
from tools.tool_manager import ToolManager, tool_manager


class TestToolManager(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Clear any existing tools to ensure clean state
        # Since ToolManager is a singleton, we need to clear the registry
        self.tool_manager = ToolManager()
        # Clear the registry by creating a new empty one
        self.tool_manager._tools_registry = {}
        
    def test_register_tool(self):
        """Test registering a tool with the manager."""
        def test_function(a: int, b: int) -> int:
            return a + b
        
        parameters = {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "First number"},
                "b": {"type": "integer", "description": "Second number"}
            },
            "required": ["a", "b"]
        }
        
        self.tool_manager.register_tool(
            name="test_add",
            description="Test addition function",
            parameters=parameters,
            fn=test_function
        )
        
        # Verify tool was registered
        tools = self.tool_manager.getTools()
        self.assertEqual(len(tools), 1)
        
        tool = tools[0]
        self.assertEqual(tool['name'], 'test_add')
        self.assertEqual(tool['description'], 'Test addition function')
        self.assertEqual(tool['parameters'], parameters)
        self.assertEqual(tool['function'], test_function)
    
    def test_get_tools_empty(self):
        """Test getting tools when none are registered."""
        tools = self.tool_manager.getTools()
        self.assertEqual(len(tools), 0)
        self.assertEqual(tools, [])
    
    def test_get_tool_info(self):
        """Test getting information about a specific tool."""
        def test_function() -> str:
            return "test"
        
        self.tool_manager.register_tool(
            name="test_tool",
            description="Test tool",
            parameters={"type": "object", "properties": {}, "required": []},
            fn=test_function
        )
        
        tool_info = self.tool_manager.get_tool_info("test_tool")
        self.assertIsNotNone(tool_info)
        self.assertEqual(tool_info['name'], 'test_tool')
        
        # Test getting info for non-existent tool
        non_existent = self.tool_manager.get_tool_info("non_existent")
        self.assertIsNone(non_existent)
    
    def test_use_tool_valid_args(self):
        """Test executing a tool with valid arguments."""
        def test_function(a: int, b: int) -> int:
            return a + b
        
        self.tool_manager.register_tool(
            name="test_add",
            description="Test addition",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"}
                },
                "required": ["a", "b"]
            },
            fn=test_function
        )
        
        result = self.tool_manager.useTool("test_add", a=5, b=3)
        self.assertEqual(result, 8)
    
    def test_use_tool_missing_required_args(self):
        """Test executing a tool with missing required arguments."""
        def test_function(a: int, b: int) -> int:
            return a + b
        
        self.tool_manager.register_tool(
            name="test_add",
            description="Test addition",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"}
                },
                "required": ["a", "b"]
            },
            fn=test_function
        )
        
        with self.assertRaises(ValueError) as context:
            self.tool_manager.useTool("test_add", a=5)  # Missing 'b'
        
        self.assertIn("Missing required parameter 'b'", str(context.exception))
    
    def test_use_tool_unknown_tool(self):
        """Test executing a non-existent tool."""
        with self.assertRaises(ValueError) as context:
            self.tool_manager.useTool("non_existent_tool", arg1="value")
        
        self.assertIn("Tool 'non_existent_tool' not found", str(context.exception))
    
    def test_use_tool_with_extra_args(self):
        """Test executing a tool with extra arguments (should be filtered)."""
        def test_function(a: int) -> int:
            return a * 2
        
        self.tool_manager.register_tool(
            name="test_double",
            description="Test doubling",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"}
                },
                "required": ["a"]
            },
            fn=test_function
        )
        
        # Should work with extra argument 'b' (it will be filtered out)
        result = self.tool_manager.useTool("test_double", a=5, b="extra")
        self.assertEqual(result, 10)
    
    def test_use_tool_execution_error(self):
        """Test handling tool execution errors."""
        def failing_function() -> None:
            raise RuntimeError("Tool execution failed")
        
        self.tool_manager.register_tool(
            name="failing_tool",
            description="Tool that always fails",
            parameters={"type": "object", "properties": {}, "required": []},
            fn=failing_function
        )
        
        with self.assertRaises(RuntimeError) as context:
            self.tool_manager.useTool("failing_tool")
        
        self.assertIn("Tool execution failed for 'failing_tool'", str(context.exception))
    
    def test_global_tool_manager_instance(self):
        """Test that the global tool_manager instance works correctly."""
        # Clear any existing tools
        original_tools = tool_manager.getTools()
        
        def test_function() -> str:
            return "global test"
        
        tool_manager.register_tool(
            name="global_test",
            description="Global test tool",
            parameters={"type": "object", "properties": {}, "required": []},
            fn=test_function
        )
        
        # Verify it's registered in the global instance
        tools = tool_manager.getTools()
        self.assertEqual(len(tools), len(original_tools) + 1)
        
        # Test using the global instance
        result = tool_manager.useTool("global_test")
        self.assertEqual(result, "global test")


if __name__ == '__main__':
    unittest.main()
