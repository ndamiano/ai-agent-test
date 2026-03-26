"""Tests for the execute_command tool."""

import json
import os
import tempfile
import time
import unittest

from tools.mock_tools import _execute_command


class TestExecuteCommand(unittest.TestCase):
    def setUp(self):
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.outputs_dir = os.path.join(self.project_root, "outputs")

    def test_simple_command_success(self):
        result = json.loads(_execute_command("echo hello"))
        self.assertTrue(result["success"])
        self.assertEqual(result["stdout"].strip(), "hello")
        self.assertEqual(result["stderr"], "")
        self.assertEqual(result["return_code"], 0)

    def test_failed_command(self):
        result = json.loads(_execute_command("ls /nonexistent_path_12345"))
        self.assertFalse(result["success"])
        self.assertNotEqual(result["return_code"], 0)
        self.assertTrue(len(result["stderr"]) > 0)

    def test_default_working_dir_is_outputs(self):
        result = json.loads(_execute_command("pwd"))
        self.assertTrue(result["success"])
        self.assertTrue(result["stdout"].strip().endswith("/outputs"))

    def test_explicit_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = json.loads(_execute_command("pwd", working_dir=tmpdir))
            self.assertTrue(result["success"])
            self.assertEqual(result["stdout"].strip(), tmpdir)

    def test_relative_working_dir(self):
        result = json.loads(_execute_command("pwd", working_dir="outputs"))
        self.assertTrue(result["success"])
        self.assertTrue(result["stdout"].strip().endswith("/outputs"))

    def test_timeout(self):
        result = json.loads(_execute_command("sleep 10", timeout=1))
        self.assertFalse(result["success"])
        self.assertEqual(result["return_code"], -1)
        self.assertIn("timed out", result["stderr"].lower())

    def test_stderr_capture(self):
        result = json.loads(_execute_command("echo error_msg >&2"))
        self.assertTrue(result["success"])
        self.assertEqual(result["stderr"].strip(), "error_msg")

    def test_multiline_stdout(self):
        result = json.loads(_execute_command("echo -e 'line1\\nline2\\nline3'"))
        self.assertTrue(result["success"])
        lines = result["stdout"].strip().split("\n")
        self.assertEqual(len(lines), 3)

    def test_exit_code_preserved(self):
        result = json.loads(_execute_command("exit 42"))
        self.assertFalse(result["success"])
        self.assertEqual(result["return_code"], 42)

    def test_create_file_in_working_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test_output.txt")
            result = json.loads(
                _execute_command(f"echo 'test content' > {test_file}", working_dir=tmpdir)
            )
            self.assertTrue(result["success"])
            self.assertTrue(os.path.exists(test_file))
            with open(test_file) as f:
                self.assertEqual(f.read().strip(), "test content")


if __name__ == "__main__":
    unittest.main()