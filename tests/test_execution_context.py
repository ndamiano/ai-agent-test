"""Tests for execution context and automatic task_id injection"""

import contextvars

from tools.execution_context import (
    execution_context,
    get_execution_context,
    get_subtask_id,
    get_task_id,
)
from tools.tool_manager import ToolManager


class TestExecutionContext:
    """Test execution context management"""

    def test_context_manager_basic(self):
        """Test that execution context sets and clears properly"""
        assert get_task_id() is None
        assert get_subtask_id() is None

        with execution_context(task_id="test-123", subtask_id="sub-456"):
            assert get_task_id() == "test-123"
            assert get_subtask_id() == "sub-456"
            assert get_execution_context()['task_id'] is not None

        # Context cleared after exiting
        assert get_task_id() is None
        assert get_subtask_id() is None
        assert get_execution_context()['task_id'] is None

    def test_partial_context(self):
        """Test context with only task_id or only subtask_id"""
        with execution_context(task_id="test-task"):
            assert get_task_id() == "test-task"
            assert get_subtask_id() is None

        with execution_context(subtask_id="test-sub"):
            assert get_task_id() is None
            assert get_subtask_id() == "test-sub"

    def test_get_execution_context(self):
        """Test getting full execution context"""
        context = get_execution_context()
        assert context == {'task_id': None, 'subtask_id': None, 'working_directory': None}

        with execution_context(task_id="t1", subtask_id="s1"):
            context = get_execution_context()
            assert context['task_id'] == 't1'
            assert context['subtask_id'] == 's1'


class TestAutoInjection:
    """Test automatic context injection in ToolManager"""

    def setup_method(self):
        """Create a fresh ToolManager for each test"""
        self.tm = ToolManager()

    def test_auto_injection_with_task_id(self):
        """Test that ToolManager auto-injects task_id"""
        # Register a tool that expects task_id
        def test_tool(task_id: str, message: str) -> str:
            return f"{task_id}: {message}"

        self.tm.register_tool(
            name="test_auto_inject",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string"}
                },
                "required": ["message"]
            },
            fn=test_tool,
            auto_inject_context=True
        )

        # Call without task_id - should be auto-injected
        with execution_context(task_id="auto-123"):
            result = self.tm.useTool("test_auto_inject", message="hello")
            assert result == "auto-123: hello"

    def test_auto_injection_with_both_ids(self):
        """Test auto-injection of both task_id and subtask_id"""
        def test_tool(task_id: str, subtask_id: str) -> str:
            return f"{task_id}/{subtask_id}"

        self.tm.register_tool(
            name="test_both_ids",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            },
            fn=test_tool,
            auto_inject_context=True
        )

        with execution_context(task_id="t1", subtask_id="s1"):
            result = self.tm.useTool("test_both_ids")
            assert result == "t1/s1"

    def test_manual_override(self):
        """Test that manually provided task_id overrides context"""
        def test_tool(task_id: str) -> str:
            return task_id

        self.tm.register_tool(
            name="test_override",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"}
                },
                "required": []
            },
            fn=test_tool,
            auto_inject_context=True
        )

        # Manual override should win
        with execution_context(task_id="context-123"):
            result = self.tm.useTool("test_override", task_id="manual-456")
            assert result == "manual-456"

    def test_no_injection_when_disabled(self):
        """Test that auto-injection can be disabled"""
        def test_tool(task_id: str = "default") -> str:
            return task_id

        self.tm.register_tool(
            name="test_no_inject",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            },
            fn=test_tool,
            auto_inject_context=False
        )

        # Should not inject even though context is available
        with execution_context(task_id="context-123"):
            result = self.tm.useTool("test_no_inject")
            assert result == "default"

    def test_no_injection_when_no_context(self):
        """Test that tools work normally when no context is available"""
        def test_tool(message: str, task_id: str = "default") -> str:
            return f"{task_id}: {message}"

        self.tm.register_tool(
            name="test_no_context",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string"}
                },
                "required": ["message"]
            },
            fn=test_tool,
            auto_inject_context=True
        )

        # Should use default value when no context
        result = self.tm.useTool("test_no_context", message="hello")
        assert result == "default: hello"

    def test_injection_skips_params_not_in_signature(self):
        """Test that injection only happens for params in function signature"""
        def test_tool(message: str) -> str:
            return message

        self.tm.register_tool(
            name="test_no_task_id_param",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string"}
                },
                "required": ["message"]
            },
            fn=test_tool,
            auto_inject_context=True
        )

        # Should not try to inject task_id since function doesn't accept it
        with execution_context(task_id="t1"):
            result = self.tm.useTool("test_no_task_id_param", message="hello")
            assert result == "hello"

    def test_validation_accepts_injected_params(self):
        """Test that validation accepts auto-injected parameters"""
        def test_tool(task_id: str, message: str) -> str:
            return f"{task_id}: {message}"

        self.tm.register_tool(
            name="test_validation",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {
                    # Note: task_id not in schema
                    "message": {"type": "string"}
                },
                "required": ["message"]
            },
            fn=test_tool,
            auto_inject_context=True
        )

        # Should pass validation even though task_id is not in schema
        with execution_context(task_id="t1"):
            result = self.tm.useTool("test_validation", message="hello")
            assert result == "t1: hello"

    def test_exit_in_different_context_does_not_raise(self):
        """The manager wraps async streaming generators that yield across context boundaries, so
        __enter__ and the finally can run in different Contexts. reset(token) raised 'Token was
        created in a different Context'; value-restore must not."""
        cm = execution_context(task_id="x")
        contextvars.copy_context().run(cm.__enter__)   # set() runs in a copied Context
        cm.__exit__(None, None, None)                  # finally runs here — must not raise

    def test_nesting_restores_outer_values(self):
        assert get_task_id() is None
        with execution_context(task_id="outer", subtask_id="a"):
            assert get_task_id() == "outer"
            with execution_context(task_id="inner", subtask_id="b"):
                assert get_task_id() == "inner"
            assert get_task_id() == "outer" and get_subtask_id() == "a"
        assert get_task_id() is None
