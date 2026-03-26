"""Tests for web_search tool implementation

Uses mocking to avoid real network calls, plus a live integration test
that can be skipped when network is unavailable.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from tools.mock_tools import _web_search


class TestWebSearchFunction:
    """Unit tests with DuckDuckGo API mocked out."""

    @patch("duckduckgo_search.DDGS")
    def test_returns_expected_json_structure(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = [
            {"title": "Example", "href": "https://example.com", "body": "An example page."},
            {"title": "Test", "href": "https://test.com", "body": "A test page."},
        ]

        raw = _web_search("python", num_results=2)
        data = json.loads(raw)

        assert data["success"] is True
        assert data["query"] == "python"
        assert len(data["results"]) == 2
        assert data["results"][0]["title"] == "Example"
        assert data["results"][0]["url"] == "https://example.com"
        assert data["results"][0]["snippet"] == "An example page."

    @patch("duckduckgo_search.DDGS")
    def test_respects_num_results(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = [{"title": f"R{i}", "href": f"https://{i}.com", "body": f"Body {i}"} for i in range(3)]

        _web_search("test", num_results=3)
        mock_instance.text.assert_called_once()
        call_kwargs = mock_instance.text.call_args
        assert call_kwargs[1]["keywords"] == "test"
        assert call_kwargs[1]["max_results"] == 3

    @patch("duckduckgo_search.DDGS")
    def test_empty_results(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = []

        raw = _web_search("xyznonexistentquery12345")
        data = json.loads(raw)

        assert data["success"] is True
        assert data["results"] == []

    @patch("duckduckgo_search.DDGS")
    def test_api_error_returns_failure(self, mock_ddgs_cls):
        mock_ddgs_cls.return_value.__enter__.side_effect = Exception("rate limited")

        raw = _web_search("test")
        data = json.loads(raw)

        assert data["success"] is False
        assert "error" in data
        assert "rate limited" in data["error"]

    @patch("duckduckgo_search.DDGS")
    def test_result_fields_default_to_empty_string(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = [{}]  # missing all fields

        raw = _web_search("test")
        data = json.loads(raw)

        assert data["success"] is True
        assert data["results"][0]["title"] == ""
        assert data["results"][0]["url"] == ""
        assert data["results"][0]["snippet"] == ""

    @patch("duckduckgo_search.DDGS")
    def test_default_num_results_is_five(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = []

        _web_search("test")
        call_kwargs = mock_instance.text.call_args
        assert call_kwargs[1]["max_results"] == 5


class TestWebSearchIntegration:
    """Live integration test – skipped when network is unavailable."""

    @pytest.mark.integration
    def test_live_search(self):
        try:
            raw = _web_search("python programming", num_results=2)
        except Exception:
            pytest.skip("Network unavailable")

        data = json.loads(raw)

        if data["success"] and data["results"]:
            assert data["query"] == "python programming"
            assert "title" in data["results"][0]
            assert "url" in data["results"][0]
            assert "snippet" in data["results"][0]
        else:
            pytest.skip(f"No results or API error: {data.get('error', 'empty results')}")