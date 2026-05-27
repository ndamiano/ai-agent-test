"""Tests for file write tracking and path containment."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock



# ── Path containment ──────────────────────────────────────────────────────────

class TestWriteToFileContainment:
    def test_relative_path_allowed(self, tmp_path):
        with patch("tools.execution_context.get_working_directory", return_value=str(tmp_path)):
            from tools.file_tools import write_to_file
            result = write_to_file("hello.txt", "content")
        assert result["success"] is True
        assert (tmp_path / "hello.txt").exists()

    def test_absolute_path_inside_allowed(self, tmp_path):
        target = str(tmp_path / "sub" / "file.txt")
        with patch("tools.execution_context.get_working_directory", return_value=str(tmp_path)):
            from tools.file_tools import write_to_file
            result = write_to_file(target, "content")
        assert result["success"] is True

    def test_absolute_path_outside_blocked(self, tmp_path):
        outside = str(tmp_path.parent / "escape.txt")
        with patch("tools.execution_context.get_working_directory", return_value=str(tmp_path)):
            from tools.file_tools import write_to_file
            result = write_to_file(outside, "content")
        assert result["success"] is False
        assert "escapes working directory" in result["error"]
        assert not Path(outside).exists()

    def test_traversal_blocked(self, tmp_path):
        with patch("tools.execution_context.get_working_directory", return_value=str(tmp_path)):
            from tools.file_tools import write_to_file
            result = write_to_file("../../etc/passwd", "evil")
        assert result["success"] is False
        assert "escapes working directory" in result["error"]

    def test_edit_outside_blocked(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("original")
        with patch("tools.execution_context.get_working_directory", return_value=str(tmp_path)):
            from tools.file_tools import edit_file
            result = edit_file(str(outside), "original", "evil")
        assert result["success"] is False
        assert "escapes working directory" in result["error"]
        assert outside.read_text() == "original"


# ── File tracking ─────────────────────────────────────────────────────────────

class TestTrackWrittenFile:
    def _make_store_mock(self):
        store = MagicMock()
        store.get_context.return_value = None
        return store

    def test_appends_path_to_task(self):
        store = self._make_store_mock()
        with patch("tools.execution_context.get_task_id", return_value="task-1"), \
             patch("database.task_store.task_store", store):
            from tools.execution_context import track_written_file
            track_written_file("/outputs/task-1/file.txt")

        store.write_context.assert_called_once()
        _, _, written_json = store.write_context.call_args[0]
        assert "/outputs/task-1/file.txt" in json.loads(written_json)

    def test_no_task_id_is_noop(self):
        store = self._make_store_mock()
        with patch("tools.execution_context.get_task_id", return_value=None), \
             patch("database.task_store.task_store", store):
            from tools.execution_context import track_written_file
            track_written_file("/some/file.txt")
        store.write_context.assert_not_called()

    def test_no_duplicates(self):
        existing = json.dumps(["/outputs/task-1/file.txt"])
        store = self._make_store_mock()
        store.get_context.return_value = existing

        with patch("tools.execution_context.get_task_id", return_value="task-1"), \
             patch("database.task_store.task_store", store):
            from tools.execution_context import track_written_file
            track_written_file("/outputs/task-1/file.txt")

        _, _, written_json = store.write_context.call_args[0]
        paths = json.loads(written_json)
        assert paths.count("/outputs/task-1/file.txt") == 1

    def test_appends_to_existing(self):
        existing = json.dumps(["/outputs/task-1/first.txt"])
        store = self._make_store_mock()
        store.get_context.return_value = existing

        with patch("tools.execution_context.get_task_id", return_value="task-1"), \
             patch("database.task_store.task_store", store):
            from tools.execution_context import track_written_file
            track_written_file("/outputs/task-1/second.txt")

        _, _, written_json = store.write_context.call_args[0]
        paths = json.loads(written_json)
        assert "/outputs/task-1/first.txt" in paths
        assert "/outputs/task-1/second.txt" in paths


class TestWriteToFileTracking:
    def test_successful_write_is_tracked(self, tmp_path):
        store = MagicMock()
        store.get_context.return_value = None

        with patch("tools.execution_context.get_working_directory", return_value=str(tmp_path)), \
             patch("tools.execution_context.get_task_id", return_value="task-1"), \
             patch("database.task_store.task_store", store):
            from tools.file_tools import write_to_file
            result = write_to_file("output.txt", "hello")

        assert result["success"] is True
        store.write_context.assert_called()
        _, _, written_json = store.write_context.call_args[0]
        tracked = json.loads(written_json)
        assert any("output.txt" in p for p in tracked)

    def test_failed_write_not_tracked(self, tmp_path):
        store = MagicMock()
        store.get_context.return_value = None
        outside = str(tmp_path.parent / "escape.txt")

        with patch("tools.execution_context.get_working_directory", return_value=str(tmp_path)), \
             patch("tools.execution_context.get_task_id", return_value="task-1"), \
             patch("database.task_store.task_store", store):
            from tools.file_tools import write_to_file
            result = write_to_file(outside, "evil")

        assert result["success"] is False
        store.write_context.assert_not_called()


class TestGetWrittenFiles:
    def test_returns_list(self):
        paths = ["/a/b.txt", "/a/c.txt"]
        store = MagicMock()
        store.get_context.return_value = json.dumps(paths)

        with patch("database.task_store.task_store", store):
            from tools.execution_context import get_written_files
            result = get_written_files("task-1")

        assert result == paths

    def test_returns_empty_when_none(self):
        store = MagicMock()
        store.get_context.return_value = None

        with patch("database.task_store.task_store", store):
            from tools.execution_context import get_written_files
            result = get_written_files("task-1")

        assert result == []
