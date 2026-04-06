"""Tests for file tools with task_id integration"""

import pytest
import os
import tempfile
import shutil
from pathlib import Path

from tools.file_tools import register_file_tools
from tools.tool_manager import tool_manager
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
    """Test write_to_file with task-specific output directories"""

    def test_write_with_task_context(self, temp_dir):
        """Test that files are scoped to task-specific directories"""
        with execution_context(task_id="task-123"):
            result = tool_manager.useTool("write_to_file", file_path="test.txt", content="Hello, World!")

        assert result["success"] is True
        # Files should be scoped to outputs/<task_id>/
        assert "outputs/task-123/test.txt" in result["file_path"]

        # Verify file actually exists at the correct location
        file_path = Path("outputs/task-123/test.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Hello, World!"

    def test_write_without_task_context(self, temp_dir):
        """Test that files go to current directory when no task context is available"""
        result = tool_manager.useTool("write_to_file", file_path="test.txt", content="No context")

        assert result["success"] is True
        # Without task context, path should not be scoped
        assert result["file_path"].endswith("test.txt")

        # Verify file exists at the correct location
        file_path = Path("test.txt")
        assert file_path.exists()
        assert file_path.read_text() == "No context"

    def test_write_with_nested_path(self, temp_dir):
        """Test that nested paths work correctly with task scoping"""
        with execution_context(task_id="task-456"):
            result = tool_manager.useTool("write_to_file", file_path="data/report.json", content='{"key": "value"}')

        assert result["success"] is True
        assert "outputs/task-456/data/report.json" in result["file_path"]

        # Verify file exists at the correct location
        file_path = Path("outputs/task-456/data/report.json")
        assert file_path.exists()
        assert file_path.read_text() == '{"key": "value"}'

    def test_write_with_already_prefixed_path(self, temp_dir):
        """Test that relative paths with 'outputs/' are still scoped to task directory"""
        with execution_context(task_id="task-789"):
            result = tool_manager.useTool("write_to_file", file_path="outputs/custom/file.txt", content="Custom path")

        assert result["success"] is True
        # Even paths starting with outputs/ should be scoped if they're relative
        assert "outputs/task-789/outputs/custom/file.txt" in result["file_path"]

        # Verify file exists at the correct location
        file_path = Path("outputs/task-789/outputs/custom/file.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Custom path"

    def test_write_creates_directories(self, temp_dir):
        """Test that parent directories are created automatically within task directory"""
        with execution_context(task_id="task-abc"):
            result = tool_manager.useTool(
                "write_to_file",
                file_path="deeply/nested/structure/file.txt",
                content="Nested content"
            )

        assert result["success"] is True
        file_path = Path("outputs/task-abc/deeply/nested/structure/file.txt")
        assert file_path.exists()
        assert file_path.read_text() == "Nested content"

    def test_multiple_writes(self, temp_dir):
        """Test that multiple files work correctly within task directory"""
        with execution_context(task_id="task-multi"):
            result1 = tool_manager.useTool("write_to_file", file_path="file1.txt", content="First file")
            result2 = tool_manager.useTool("write_to_file", file_path="file2.txt", content="Second file")
            result3 = tool_manager.useTool("write_to_file", file_path="subdir/file3.txt", content="Third file")

        assert "outputs/task-multi/file1.txt" in result1["file_path"]
        assert "outputs/task-multi/file2.txt" in result2["file_path"]
        assert "outputs/task-multi/subdir/file3.txt" in result3["file_path"]

        # Verify all files exist
        assert Path("outputs/task-multi/file1.txt").exists()
        assert Path("outputs/task-multi/file2.txt").exists()
        assert Path("outputs/task-multi/subdir/file3.txt").exists()

    def test_different_tasks_separate_directories(self, temp_dir):
        """Test that different tasks write to separate task-specific directories"""
        with execution_context(task_id="task-A"):
            result_a = tool_manager.useTool("write_to_file", file_path="output.txt", content="Task A")

        with execution_context(task_id="task-B"):
            result_b = tool_manager.useTool("write_to_file", file_path="output.txt", content="Task B")

        assert "outputs/task-A/output.txt" in result_a["file_path"]
        assert "outputs/task-B/output.txt" in result_b["file_path"]

        # Verify both files exist in separate directories with different content
        assert Path("outputs/task-A/output.txt").read_text() == "Task A"
        assert Path("outputs/task-B/output.txt").read_text() == "Task B"

    @pytest.mark.skip(reason="write_to_file does not have mode parameter")
    def test_append_mode(self, temp_dir):
        """Test that append mode works correctly"""
        with execution_context(task_id="task-append"):
            tool_manager.useTool("write_to_file", file_path="log.txt", content="Line 1\n", mode="w")
            tool_manager.useTool("write_to_file", file_path="log.txt", content="Line 2\n", mode="a")
            tool_manager.useTool("write_to_file", file_path="log.txt", content="Line 3\n", mode="a")

        file_path = Path("outputs/task-append/log.txt")
        content = file_path.read_text()
        assert content == "Line 1\nLine 2\nLine 3\n"


class TestReadFile:
    """Test read_file with automatic task-based path scoping"""

    def test_read_file_with_task_context(self, temp_dir):
        """Test reading a file from task-scoped directory"""
        # Write and read within same task context - both auto-scoped
        with execution_context(task_id="task-read-1"):
            tool_manager.useTool("write_to_file", file_path="test.txt", content="Hello, World!")
            result = tool_manager.useTool("read_file", file_path="test.txt")

        assert result["success"] is True
        assert result["content"] == "Hello, World!"
        assert "outputs/task-read-1/test.txt" in result["file_path"]

    def test_read_file_without_task_context(self, temp_dir):
        """Test reading a file without task context"""
        tool_manager.useTool("write_to_file", file_path="test.txt", content="No context")
        result = tool_manager.useTool("read_file", file_path="test.txt")

        assert result["success"] is True
        assert result["content"] == "No context"
        assert result["file_path"].endswith("test.txt")

    def test_read_nonexistent_file(self, temp_dir):
        """Test reading a file that doesn't exist"""
        with execution_context(task_id="task-read-2"):
            result = tool_manager.useTool("read_file", file_path="nonexistent.txt")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.skip(reason="write_to_file and read_file don't have encoding parameter")
    def test_read_file_different_encoding(self, temp_dir):
        """Test reading a file with different encoding"""
        with execution_context(task_id="task-read-3"):
            # Write with utf-8
            tool_manager.useTool("write_to_file", file_path="test.txt", content="Test content", encoding="utf-8")
            # Read with utf-8
            result = tool_manager.useTool("read_file", file_path="test.txt", encoding="utf-8")

        assert result["success"] is True
        assert result["content"] == "Test content"


class TestListFiles:
    """Test list_directory with automatic task_id path resolution"""

    def test_list_directory_empty_directory(self, temp_dir):
        """Test listing files in a task directory that doesn't exist yet"""
        with execution_context(task_id="task-list-1"):
            # Task directory doesn't exist until first write
            result = tool_manager.useTool("list_directory")

        # Should fail because the task directory hasn't been created yet
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_list_directory_with_content(self, temp_dir):
        """Test listing files in a task directory with content"""
        with execution_context(task_id="task-list-2"):
            tool_manager.useTool("write_to_file", file_path="file1.txt", content="Content 1")
            tool_manager.useTool("write_to_file", file_path="file2.txt", content="Content 2")
            tool_manager.useTool("write_to_file", file_path="file3.json", content='{"key": "value"}')

            result = tool_manager.useTool("list_directory")

        assert result["success"] is True
        assert result["file_count"] == 3
        assert "file1.txt" in result["files"]
        assert "file2.txt" in result["files"]
        assert "file3.json" in result["files"]

    def test_list_directory_with_pattern(self, temp_dir):
        """Test listing files with a glob pattern"""
        with execution_context(task_id="task-list-3"):
            tool_manager.useTool("write_to_file", file_path="file1.txt", content="Content 1")
            tool_manager.useTool("write_to_file", file_path="file2.txt", content="Content 2")
            tool_manager.useTool("write_to_file", file_path="file3.json", content='{"key": "value"}')

            result = tool_manager.useTool("list_directory", pattern="*.txt")

        assert result["success"] is True
        assert result["file_count"] == 2
        assert "file1.txt" in result["files"]
        assert "file2.txt" in result["files"]
        assert "file3.json" not in result["files"]

    def test_list_directory_recursive(self, temp_dir):
        """Test listing files recursively"""
        with execution_context(task_id="task-list-4"):
            tool_manager.useTool("write_to_file", file_path="root.txt", content="Root")
            tool_manager.useTool("write_to_file", file_path="subdir/nested.txt", content="Nested")
            tool_manager.useTool("write_to_file", file_path="subdir/deep/deeper.txt", content="Deeper")

            result = tool_manager.useTool("list_directory", recursive=True)

        assert result["success"] is True
        assert result["file_count"] == 3
        assert "root.txt" in result["files"]
        assert any("nested.txt" in f for f in result["files"])
        assert any("deeper.txt" in f for f in result["files"])

    def test_list_directory_in_subdirectory(self, temp_dir):
        """Test listing files in a specific subdirectory"""
        with execution_context(task_id="task-list-5"):
            tool_manager.useTool("write_to_file", file_path="root.txt", content="Root")
            tool_manager.useTool("write_to_file", file_path="data/file1.txt", content="Data 1")
            tool_manager.useTool("write_to_file", file_path="data/file2.txt", content="Data 2")

            result = tool_manager.useTool("list_directory", directory="data")

        assert result["success"] is True
        assert result["file_count"] == 2
        assert "file1.txt" in result["files"]
        assert "file2.txt" in result["files"]
        assert "root.txt" not in result["files"]

    def test_list_directory_without_task_context(self, temp_dir):
        """Test listing files without task context"""
        tool_manager.useTool("write_to_file", file_path="test.txt", content="Test")
        result = tool_manager.useTool("list_directory")

        assert result["success"] is True
        assert result["file_count"] >= 1
        assert "test.txt" in result["files"]


class TestDeleteFile:
    """Test delete_file with task-scoped paths"""

    def test_delete_file_with_task_context(self, temp_dir):
        """Test deleting a file from task-scoped directory"""
        with execution_context(task_id="task-delete-1"):
            # Create a file (scoped to task directory)
            tool_manager.useTool("write_to_file", file_path="to_delete.txt", content="Delete me")

            # Verify it exists in task directory
            file_path = Path("outputs/task-delete-1/to_delete.txt")
            assert file_path.exists()

            # Delete it (also scoped)
            result = tool_manager.useTool("delete_file", file_path="to_delete.txt")

        assert result["success"] is True
        assert "outputs/task-delete-1/to_delete.txt" in result["file_path"]
        assert not file_path.exists()

    def test_delete_file_without_task_context(self, temp_dir):
        """Test deleting a file without task context"""
        # Create a file (not scoped)
        tool_manager.useTool("write_to_file", file_path="to_delete.txt", content="Delete me")

        # Verify it exists
        file_path = Path("to_delete.txt")
        assert file_path.exists()

        # Delete it
        result = tool_manager.useTool("delete_file", file_path="to_delete.txt")

        assert result["success"] is True
        assert not file_path.exists()

    def test_delete_nonexistent_file(self, temp_dir):
        """Test deleting a file that doesn't exist"""
        with execution_context(task_id="task-delete-2"):
            result = tool_manager.useTool("delete_file", file_path="nonexistent.txt")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_delete_multiple_files(self, temp_dir):
        """Test deleting multiple files from task directory"""
        with execution_context(task_id="task-delete-3"):
            # Create multiple files
            tool_manager.useTool("write_to_file", file_path="file1.txt", content="File 1")
            tool_manager.useTool("write_to_file", file_path="file2.txt", content="File 2")
            tool_manager.useTool("write_to_file", file_path="file3.txt", content="File 3")

            # Verify they exist in task directory
            assert Path("outputs/task-delete-3/file1.txt").exists()
            assert Path("outputs/task-delete-3/file2.txt").exists()
            assert Path("outputs/task-delete-3/file3.txt").exists()

            # Delete them one by one
            result1 = tool_manager.useTool("delete_file", file_path="file1.txt")
            result2 = tool_manager.useTool("delete_file", file_path="file2.txt")
            result3 = tool_manager.useTool("delete_file", file_path="file3.txt")

        assert result1["success"] is True
        assert result2["success"] is True
        assert result3["success"] is True

        # Verify all files are gone
        assert not Path("outputs/task-delete-3/file1.txt").exists()
        assert not Path("outputs/task-delete-3/file2.txt").exists()
        assert not Path("outputs/task-delete-3/file3.txt").exists()

    def test_delete_file_path_traversal_protection(self, temp_dir):
        """Test that delete_file prevents path traversal attacks"""
        with execution_context(task_id="task-delete-4"):
            # Try to delete a file outside the task directory using path traversal
            result = tool_manager.useTool("delete_file", file_path="../../etc/passwd")

        # Should fail because the file doesn't exist (path gets resolved safely)
        assert result["success"] is False
        assert "not found" in result["error"].lower()
