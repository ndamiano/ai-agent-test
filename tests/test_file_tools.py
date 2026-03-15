"""Tests for file tools with task_id integration"""

import pytest
import os
import tempfile
import shutil
from pathlib import Path

from tools.file_tools import write_to_file, register_file_tools
from tools.tool_manager import ToolManager, tool_manager
from tools.execution_context import execution_context


@pytest.fixture(scope="function")
def temp_dir():
    """Create a temporary directory for test outputs"""
    original_cwd = os.getcwd()
    test_dir = tempfile.mkdtemp()
    os.chdir(test_dir)
    yield test_dir
    os.chdir(original_cwd)
    shutil.rmtree(test_dir, ignore_errors=True)


@pytest.fixture(scope="function", autouse=True)
def ensure_tool_registered():
    """Ensure write_to_file tool is registered before each test"""
    # Register file tools if not already registered
    if 'write_to_file' not in tool_manager._tools_registry:
        register_file_tools()
    yield
    # No cleanup needed - leave tools registered for other tests


class TestWriteToFileWithTaskId:
    """Test write_to_file with automatic task_id path prepending"""

    def test_write_with_task_context(self, temp_dir):
        """Test that files are organized by task_id when context is available"""
        with execution_context(task_id="task-123"):
            result = tool_manager.useTool("write_to_file", path="test.txt", content="Hello, World!")

        assert result["success"] is True
        assert "task-123" in result["file_path"]
        assert result["file_path"] == "outputs/task-123/test.txt"

        # Verify file actually exists at the correct location
        file_path = Path("outputs/task-123/test.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Hello, World!"

    def test_write_without_task_context(self, temp_dir):
        """Test that files go to outputs/ when no task context is available"""
        result = tool_manager.useTool("write_to_file", path="test.txt", content="No context")

        assert result["success"] is True
        assert result["file_path"] == "outputs/test.txt"

        # Verify file exists at the correct location
        file_path = Path("outputs/test.txt")
        assert file_path.exists()
        assert file_path.read_text() == "No context"

    def test_write_with_nested_path(self, temp_dir):
        """Test that nested paths work correctly with task_id"""
        with execution_context(task_id="task-456"):
            result = tool_manager.useTool("write_to_file", path="data/report.json", content='{"key": "value"}')

        assert result["success"] is True
        assert result["file_path"] == "outputs/task-456/data/report.json"

        # Verify file exists at the correct location
        file_path = Path("outputs/task-456/data/report.json")
        assert file_path.exists()
        assert file_path.read_text() == '{"key": "value"}'

    def test_write_with_already_prefixed_path(self, temp_dir):
        """Test that paths already starting with 'outputs/' are not modified"""
        with execution_context(task_id="task-789"):
            result = tool_manager.useTool("write_to_file", path="outputs/custom/file.txt", content="Custom path")

        assert result["success"] is True
        # Should not add task_id since path already starts with outputs/
        assert result["file_path"] == "outputs/custom/file.txt"

        # Verify file exists at the correct location
        file_path = Path("outputs/custom/file.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Custom path"

    def test_write_creates_task_directories(self, temp_dir):
        """Test that parent directories including task_id are created automatically"""
        with execution_context(task_id="task-abc"):
            result = tool_manager.useTool(
                "write_to_file",
                path="deeply/nested/structure/file.txt",
                content="Nested content"
            )

        assert result["success"] is True
        file_path = Path("outputs/task-abc/deeply/nested/structure/file.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Nested content"

    def test_multiple_writes_same_task(self, temp_dir):
        """Test that multiple files in same task go to same task directory"""
        with execution_context(task_id="task-multi"):
            result1 = tool_manager.useTool("write_to_file", path="file1.txt", content="First file")
            result2 = tool_manager.useTool("write_to_file", path="file2.txt", content="Second file")
            result3 = tool_manager.useTool("write_to_file", path="subdir/file3.txt", content="Third file")

        assert result1["file_path"] == "outputs/task-multi/file1.txt"
        assert result2["file_path"] == "outputs/task-multi/file2.txt"
        assert result3["file_path"] == "outputs/task-multi/subdir/file3.txt"

        # Verify all files exist
        assert Path("outputs/task-multi/file1.txt").exists()
        assert Path("outputs/task-multi/file2.txt").exists()
        assert Path("outputs/task-multi/subdir/file3.txt").exists()

    def test_different_tasks_different_directories(self, temp_dir):
        """Test that different tasks create separate directories"""
        with execution_context(task_id="task-A"):
            result_a = tool_manager.useTool("write_to_file", path="output.txt", content="Task A")

        with execution_context(task_id="task-B"):
            result_b = tool_manager.useTool("write_to_file", path="output.txt", content="Task B")

        assert result_a["file_path"] == "outputs/task-A/output.txt"
        assert result_b["file_path"] == "outputs/task-B/output.txt"

        # Verify both files exist with different content
        assert Path("outputs/task-A/output.txt").read_text() == "Task A"
        assert Path("outputs/task-B/output.txt").read_text() == "Task B"

    def test_append_mode_with_task_id(self, temp_dir):
        """Test that append mode works correctly with task_id paths"""
        with execution_context(task_id="task-append"):
            result1 = tool_manager.useTool("write_to_file", path="log.txt", content="Line 1\n", mode="w")
            result2 = tool_manager.useTool("write_to_file", path="log.txt", content="Line 2\n", mode="a")
            result3 = tool_manager.useTool("write_to_file", path="log.txt", content="Line 3\n", mode="a")

        file_path = Path("outputs/task-append/log.txt")
        content = file_path.read_text()
        assert content == "Line 1\nLine 2\nLine 3\n"
