"""Tests verifying shared base connector methods work identically on both subclasses"""

import unittest
from llm_clients.base_connector import BaseConnector
from llm_clients.lmstudio_client import LMStudioConnector
from llm_clients.cline_client import ClineConnector
import requests


class TestBaseConnectorShared(unittest.TestCase):
    """Verify contextualize, _format_dict, _get_session produce identical behavior on both subclasses."""

    @classmethod
    def setUpClass(cls):
        cls.lmstudio = LMStudioConnector(settings={
            "base_url": "http://localhost:1234",
            "model": "test-model",
        })
        cls.cline = ClineConnector(settings={
            "api_key": "test-key",
            "model": "test-model",
        })
        cls.connectors = {
            "lmstudio": cls.lmstudio,
            "cline": cls.cline,
        }

    # --- contextualize ---------------------------------------------------------

    def test_contextualize_none(self):
        for name, conn in self.connectors.items():
            self.assertEqual(conn.contextualize(None), "", f"{name}: contextualize(None) should be empty")

    def test_contextualize_plain_string(self):
        for name, conn in self.connectors.items():
            self.assertEqual(conn.contextualize("hello"), "hello", f"{name}: plain string mismatch")

    def test_contextualize_dict(self):
        d = {"key": "value", "num": 42}
        for name, conn in self.connectors.items():
            result = conn.contextualize(d)
            self.assertIn("Data:", result)
            self.assertIn("key: value", result)
            self.assertIn("num: 42", result)

    def test_contextualize_object(self):
        class Dummy:
            def __init__(self):
                self.x = 1
                self.y = "two"
        obj = Dummy()
        results = {}
        for name, conn in self.connectors.items():
            results[name] = conn.contextualize(obj)
        self.assertEqual(results["lmstudio"], results["cline"], "contextualize on objects should match")

    def test_contextualize_list(self):
        items = [{"a": 1}, "plain", [1, 2]]
        results = {}
        for name, conn in self.connectors.items():
            results[name] = conn.contextualize(items)
        self.assertEqual(results["lmstudio"], results["cline"], "contextualize on list should match")

    # --- _format_dict ----------------------------------------------------------

    def test_format_dict_basic(self):
        d = {"name": "test", "count": 5}
        for name, conn in self.connectors.items():
            result = conn._format_dict(d, "Info")
            self.assertIn("Info:", result)
            self.assertIn("name: test", result)
            self.assertIn("count: 5", result)

    def test_format_dict_list_short(self):
        d = {"tags": ["a", "b", "c"]}
        results = {}
        for name, conn in self.connectors.items():
            results[name] = conn._format_dict(d)
        self.assertEqual(results["lmstudio"], results["cline"], "short list formatting should match")

    def test_format_dict_list_long(self):
        d = {"items": list(range(10))}
        results = {}
        for name, conn in self.connectors.items():
            results[name] = conn._format_dict(d)
        self.assertEqual(results["lmstudio"], results["cline"], "long list formatting should match")

    def test_format_dict_nested(self):
        d = {"nested": {"a": 1}}
        results = {}
        for name, conn in self.connectors.items():
            results[name] = conn._format_dict(d)
        self.assertEqual(results["lmstudio"], results["cline"], "nested dict formatting should match")

    # --- _get_session ----------------------------------------------------------

    def test_get_session_returns_requests_session(self):
        for name, conn in self.connectors.items():
            session = conn._get_session()
            self.assertIsInstance(session, requests.Session, f"{name}: _get_session should return a requests.Session")

    def test_get_session_is_cached(self):
        for name, conn in self.connectors.items():
            s1 = conn._get_session()
            s2 = conn._get_session()
            self.assertIs(s1, s2, f"{name}: _get_session should return the same cached instance")

    # --- connector_name --------------------------------------------------------

    def test_connector_name_set(self):
        self.assertEqual(self.lmstudio.connector_name, "lmstudio")
        self.assertEqual(self.cline.connector_name, "cline")

    def test_inheritance(self):
        for name, conn in self.connectors.items():
            self.assertIsInstance(conn, BaseConnector, f"{name} should inherit from BaseConnector")


if __name__ == "__main__":
    unittest.main()
