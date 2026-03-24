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
    """Test write_to_file with direct outputs/ path (no task_id subdirectory)"""

    def test_write_with_task_context(self, temp_dir):
        """Test that files go to outputs/ directly (task_id is ignored)"""
        with execution_context(task_id="task-123"):
            result = tool_manager.useTool("write_to_file", path="test.txt", content="Hello, World!")

        assert result["success"] is True
        assert result["file_path"] == "outputs/test.txt"

        # Verify file actually exists at the correct location
        file_path = Path("outputs/test.txt")
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
        """Test that nested paths work correctly"""
        with execution_context(task_id="task-456"):
            result = tool_manager.useTool("write_to_file", path="data/report.json", content='{"key": "value"}')

        assert result["success"] is True
        assert result["file_path"] == "outputs/data/report.json"

        # Verify file exists at the correct location
        file_path = Path("outputs/data/report.json")
        assert file_path.exists()
        assert file_path.read_text() == '{"key": "value"}'

    def test_write_with_already_prefixed_path(self, temp_dir):
        """Test that paths already starting with 'outputs/' are not modified"""
        with execution_context(task_id="task-789"):
            result = tool_manager.useTool("write_to_file", path="outputs/custom/file.txt", content="Custom path")

        assert result["success"] is True
        assert result["file_path"] == "outputs/custom/file.txt"

        # Verify file exists at the correct location
        file_path = Path("outputs/custom/file.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Custom path"

    def test_write_creates_directories(self, temp_dir):
        """Test that parent directories are created automatically"""
        with execution_context(task_id="task-abc"):
            result = tool_manager.useTool(
                "write_to_file",
                path="deeply/nested/structure/file.txt",
                content="Nested content"
            )

        assert result["success"] is True
        file_path = Path("outputs/deeply/nested/structure/file.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Nested content"

    def test_multiple_writes(self, temp_dir):
        """Test that multiple files work correctly"""
        with execution_context(task_id="task-multi"):
            result1 = tool_manager.useTool("write_to_file", path="file1.txt", content="First file")
            result2 = tool_manager.useTool("write_to_file", path="file2.txt", content="Second file")
            result3 = tool_manager.useTool("write_to_file", path="subdir/file3.txt", content="Third file")

        assert result1["file_path"] == "outputs/file1.txt"
        assert result2["file_path"] == "outputs/file2.txt"
        assert result3["file_path"] == "outputs/subdir/file3.txt"

        # Verify all files exist
        assert Path("outputs/file1.txt").exists()
        assert Path("outputs/file2.txt").exists()
        assert Path("outputs/subdir/file3.txt").exists()

    def test_different_tasks_same_directory(self, temp_dir):
        """Test that different tasks write to the same outputs/ directory"""
        with execution_context(task_id="task-A"):
            result_a = tool_manager.useTool("write_to_file", path="output.txt", content="Task A")

        with execution_context(task_id="task-B"):
            result_b = tool_manager.useTool("write_to_file", path="output2.txt", content="Task B")

        assert result_a["file_path"] == "outputs/output.txt"
        assert result_b["file_path"] == "outputs/output2.txt"

        # Verify both files exist with different content
        assert Path("outputs/output.txt").read_text() == "Task A"
        assert Path("outputs/output2.txt").read_text() == "Task B"

    def test_append_mode(self, temp_dir):
        """Test that append mode works correctly"""
        with execution_context(task_id="task-append"):
            result1 = tool_manager.useTool("write_to_file", path="log.txt", content="Line 1\n", mode="w")
            result2 = tool_manager.useTool("write_to_file", path="log.txt", content="Line 2\n", mode="a")
            result3 = tool_manager.useTool("write_to_file", path="log.txt", content="Line 3\n", mode="a")

        file_path = Path("outputs/log.txt")
        content = file_path.read_text()
        assert content == "Line 1\nLine 2\nLine 3\n"


class TestReadFile:
    """Test read_file with direct outputs/ path (no task_id subdirectory)"""

    def test_read_file_with_task_context(self, temp_dir):
        """Test reading a file (task_id is ignored)"""
        # First write a file
        with execution_context(task_id="task-read-1"):
            tool_manager.useTool("write_to_file", path="test.txt", content="Hello, World!")
            result = tool_manager.useTool("read_file", path="test.txt")

        assert result["success"] is True
        assert result["content"] == "Hello, World!"
        assert result["file_path"] == "outputs/test.txt"

    def test_read_file_without_task_context(self, temp_dir):
        """Test reading a file without task context"""
        tool_manager.useTool("write_to_file", path="test.txt", content="No context")
        result = tool_manager.useTool("read_file", path="test.txt")

        assert result["success"] is True
        assert result["content"] == "No context"
        assert result["file_path"] == "outputs/test.txt"

    def test_read_nonexistent_file(self, temp_dir):
        """Test reading a file that doesn't exist"""
        with execution_context(task_id="task-read-2"):
            result = tool_manager.useTool("read_file", path="nonexistent.txt")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_read_file_different_encoding(self, temp_dir):
        """Test reading a file with different encoding"""
        with execution_context(task_id="task-read-3"):
            # Write with utf-8
            tool_manager.useTool("write_to_file", path="test.txt", content="Test content", encoding="utf-8")
            # Read with utf-8
            result = tool_manager.useTool("read_file", path="test.txt", encoding="utf-8")

        assert result["success"] is True
        assert result["content"] == "Test content"


class TestListFiles:
    """Test list_files with automatic task_id path resolution"""

    def test_list_files_empty_directory(self, temp_dir):
        """Test listing files in an empty directory"""
        with execution_context(task_id="task-list-1"):
            result = tool_manager.useTool("list_files")

        assert result["success"] is True
        assert result["files"] == []
        assert result["count"] == 0

    def test_list_files_with_content(self, temp_dir):
        """Test listing files in a directory with content"""
        with execution_context(task_id="task-list-2"):
            tool_manager.useTool("write_to_file", path="file1.txt", content="Content 1")
            tool_manager.useTool("write_to_file", path="file2.txt", content="Content 2")
            tool_manager.useTool("write_to_file", path="file3.json", content='{"key": "value"}')

            result = tool_manager.useTool("list_files")

        assert result["success"] is True
        assert result["count"] == 3
        assert "file1.txt" in result["files"]
        assert "file2.txt" in result["files"]
        assert "file3.json" in result["files"]

    def test_list_files_with_pattern(self, temp_dir):
        """Test listing files with a glob pattern"""
        with execution_context(task_id="task-list-3"):
            tool_manager.useTool("write_to_file", path="file1.txt", content="Content 1")
            tool_manager.useTool("write_to_file", path="file2.txt", content="Content 2")
            tool_manager.useTool("write_to_file", path="file3.json", content='{"key": "value"}')

            result = tool_manager.useTool("list_files", pattern="*.txt")

        assert result["success"] is True
        assert result["count"] == 2
        assert "file1.txt" in result["files"]
        assert "file2.txt" in result["files"]
        assert "file3.json" not in result["files"]

    def test_list_files_recursive(self, temp_dir):
        """Test listing files recursively"""
        with execution_context(task_id="task-list-4"):
            tool_manager.useTool("write_to_file", path="root.txt", content="Root")
            tool_manager.useTool("write_to_file", path="subdir/nested.txt", content="Nested")
            tool_manager.useTool("write_to_file", path="subdir/deep/deeper.txt", content="Deeper")

            result = tool_manager.useTool("list_files", recursive=True)

        assert result["success"] is True
        assert result["count"] == 3
        assert "root.txt" in result["files"]
        assert any("nested.txt" in f for f in result["files"])
        assert any("deeper.txt" in f for f in result["files"])

    def test_list_files_in_subdirectory(self, temp_dir):
        """Test listing files in a specific subdirectory"""
        with execution_context(task_id="task-list-5"):
            tool_manager.useTool("write_to_file", path="root.txt", content="Root")
            tool_manager.useTool("write_to_file", path="data/file1.txt", content="Data 1")
            tool_manager.useTool("write_to_file", path="data/file2.txt", content="Data 2")

            result = tool_manager.useTool("list_files", path="data")

        assert result["success"] is True
        assert result["count"] == 2
        assert "file1.txt" in result["files"]
        assert "file2.txt" in result["files"]
        assert "root.txt" not in result["files"]

    def test_list_files_without_task_context(self, temp_dir):
        """Test listing files without task context"""
        tool_manager.useTool("write_to_file", path="test.txt", content="Test")
        result = tool_manager.useTool("list_files")

        assert result["success"] is True
        assert result["count"] >= 1
        assert "test.txt" in result["files"]


class TestDeleteFile:
    """Test delete_file with direct outputs/ path (no task_id subdirectory)"""

    def test_delete_file_with_task_context(self, temp_dir):
        """Test deleting a file (task_id is ignored)"""
        with execution_context(task_id="task-delete-1"):
            # Create a file
            tool_manager.useTool("write_to_file", path="to_delete.txt", content="Delete me")

            # Verify it exists
            file_path = Path("outputs/to_delete.txt")
            assert file_path.exists()

            # Delete it
            result = tool_manager.useTool("delete_file", path="to_delete.txt")

        assert result["success"] is True
        assert result["file_path"] == "outputs/to_delete.txt"
        assert not file_path.exists()

    def test_delete_file_without_task_context(self, temp_dir):
        """Test deleting a file without task context"""
        # Create a file
        tool_manager.useTool("write_to_file", path="to_delete.txt", content="Delete me")

        # Verify it exists
        file_path = Path("outputs/to_delete.txt")
        assert file_path.exists()

        # Delete it
        result = tool_manager.useTool("delete_file", path="to_delete.txt")

        assert result["success"] is True
        assert not file_path.exists()

    def test_delete_nonexistent_file(self, temp_dir):
        """Test deleting a file that doesn't exist"""
        with execution_context(task_id="task-delete-2"):
            result = tool_manager.useTool("delete_file", path="nonexistent.txt")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_delete_multiple_files(self, temp_dir):
        """Test deleting multiple files"""
        with execution_context(task_id="task-delete-3"):
            # Create multiple files
            tool_manager.useTool("write_to_file", path="file1.txt", content="File 1")
            tool_manager.useTool("write_to_file", path="file2.txt", content="File 2")
            tool_manager.useTool("write_to_file", path="file3.txt", content="File 3")

            # Verify they exist
            assert Path("outputs/file1.txt").exists()
            assert Path("outputs/file2.txt").exists()
            assert Path("outputs/file3.txt").exists()

            # Delete them one by one
            result1 = tool_manager.useTool("delete_file", path="file1.txt")
            result2 = tool_manager.useTool("delete_file", path="file2.txt")
            result3 = tool_manager.useTool("delete_file", path="file3.txt")

        assert result1["success"] is True
        assert result2["success"] is True
        assert result3["success"] is True

        # Verify all files are gone
        assert not Path("outputs/file1.txt").exists()
        assert not Path("outputs/file2.txt").exists()
        assert not Path("outputs/file3.txt").exists()

    def test_delete_file_safety_check(self, temp_dir):
        """Test that delete_file only works within outputs directory"""
        with execution_context(task_id="task-delete-4"):
            # Try to delete a file outside outputs (should fail)
            result = tool_manager.useTool("delete_file", path="outputs/../../etc/passwd")

        assert result["success"] is False
        assert "outputs directory" in result["error"].lower()
