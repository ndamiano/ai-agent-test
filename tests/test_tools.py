"""Tests for ToolManager"""

import pytest

from tools.tool_manager import ToolManager, tool_manager


@pytest.fixture
def manager():
    """A ToolManager with a clean registry.

    Since ToolManager is a singleton, we clear the registry to ensure clean state.
    """
    mgr = ToolManager()
    mgr._tools_registry = {}
    return mgr


def test_register_tool(manager):
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

    manager.register_tool(
        name="test_add",
        description="Test addition function",
        parameters=parameters,
        fn=test_function
    )

    # Verify tool was registered
    tools = manager.getTools()
    assert len(tools) == 1

    tool = tools[0]
    assert tool['name'] == 'test_add'
    assert tool['description'] == 'Test addition function'
    assert tool['parameters'] == parameters
    assert tool['function'] == test_function


def test_get_tools_empty(manager):
    """Test getting tools when none are registered."""
    tools = manager.getTools()
    assert len(tools) == 0
    assert tools == []


def test_get_tool_info(manager):
    """Test getting information about a specific tool."""
    def test_function() -> str:
        return "test"

    manager.register_tool(
        name="test_tool",
        description="Test tool",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=test_function
    )

    tool_info = manager.get_tool_info("test_tool")
    assert tool_info is not None
    assert tool_info['name'] == 'test_tool'

    # Test getting info for non-existent tool
    non_existent = manager.get_tool_info("non_existent")
    assert non_existent is None


def test_use_tool_valid_args(manager):
    """Test executing a tool with valid arguments."""
    def test_function(a: int, b: int) -> int:
        return a + b

    manager.register_tool(
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

    result = manager.useTool("test_add", a=5, b=3)
    assert result == 8


def test_use_tool_missing_required_args(manager):
    """Test executing a tool with missing required arguments."""
    def test_function(a: int, b: int) -> int:
        return a + b

    manager.register_tool(
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

    with pytest.raises(ValueError) as exc_info:
        manager.useTool("test_add", a=5)  # Missing 'b'

    assert "Missing required parameter 'b'" in str(exc_info.value)


def test_use_tool_unknown_tool(manager):
    """Test executing a non-existent tool."""
    with pytest.raises(ValueError) as exc_info:
        manager.useTool("non_existent_tool", arg1="value")

    assert "Tool 'non_existent_tool' not found" in str(exc_info.value)


def test_use_tool_with_extra_args(manager):
    """Test executing a tool with extra arguments (should be filtered)."""
    def test_function(a: int) -> int:
        return a * 2

    manager.register_tool(
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
    result = manager.useTool("test_double", a=5, b="extra")
    assert result == 10


def test_use_tool_execution_error(manager):
    """Test handling tool execution errors."""
    def failing_function() -> None:
        raise RuntimeError("Tool execution failed")

    manager.register_tool(
        name="failing_tool",
        description="Tool that always fails",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=failing_function
    )

    with pytest.raises(RuntimeError) as exc_info:
        manager.useTool("failing_tool")

    assert "Tool execution failed for 'failing_tool'" in str(exc_info.value)


def test_global_tool_manager_instance():
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
    assert len(tools) == len(original_tools) + 1

    # Test using the global instance
    result = tool_manager.useTool("global_test")
    assert result == "global test"
