# tests/test_connection_pooling.py
"""Verify that all HTTP methods use _get_session() for connection pooling."""

import unittest
from unittest.mock import patch, MagicMock


class TestClineConnectionPooling(unittest.TestCase):
    """Test that ClineConnector uses _get_session() consistently."""

    def _make_connector(self):
        from llm_clients.cline_client import ClineConnector
        connector = ClineConnector.__new__(ClineConnector)
        connector.api_key = "test-key"
        connector.model_name = "test-model"
        connector.temperature = 0.7
        connector.max_tokens = 100
        connector.base_url = "https://api.cline.bot"
        connector.api_endpoint = f"{connector.base_url}/api/v1/chat/completions"
        connector._connected = True
        connector._session = None
        from threading import Lock
        connector._session_lock = Lock()
        return connector

    def test_generate_uses_session(self):
        connector = self._make_connector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"choices": [{"message": {"content": "hello"}}]}
        }
        with patch.object(connector, '_get_session') as mock_get_session:
            mock_session = MagicMock()
            mock_session.post.return_value = mock_response
            mock_get_session.return_value = mock_session
            result = connector.generate("prompt", "context")
            mock_get_session.assert_called_once()
            mock_session.post.assert_called_once()

    def test_generate_with_tools_uses_session(self):
        connector = self._make_connector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"choices": [{"message": {"content": "hello"}}]}
        }
        with patch.object(connector, '_get_session') as mock_get_session:
            mock_session = MagicMock()
            mock_session.post.return_value = mock_response
            mock_get_session.return_value = mock_session
            result = connector.generate_with_tools([{"role": "user", "content": "hi"}])
            mock_get_session.assert_called_once()
            mock_session.post.assert_called_once()

    def test_generate_with_tools_stream_uses_session(self):
        connector = self._make_connector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = [
            b'data: {"data": {"choices": [{"delta": {"content": "hi"}}]}}',
            b'data: [DONE]'
        ]
        with patch.object(connector, '_get_session') as mock_get_session:
            mock_session = MagicMock()
            mock_session.post.return_value = mock_response
            mock_get_session.return_value = mock_session
            chunks = list(connector.generate_with_tools_stream([{"role": "user", "content": "hi"}]))
            mock_get_session.assert_called_once()
            mock_session.post.assert_called_once()


class TestLMStudioConnectionPooling(unittest.TestCase):
    """Test that LMStudioConnector uses _get_session() consistently."""

    def _make_connector(self):
        from llm_clients.lmstudio_client import LMStudioConnector
        connector = LMStudioConnector.__new__(LMStudioConnector)
        connector.api_endpoint = "http://localhost:1234/v1/chat/completions"
        connector.model_name = "test-model"
        connector.temperature = 0.7
        connector.max_tokens = 100
        connector._session = None
        from threading import Lock
        connector._session_lock = Lock()
        return connector

    def test_generate_uses_session(self):
        connector = self._make_connector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hello"}}]
        }
        with patch.object(connector, '_get_session') as mock_get_session:
            mock_session = MagicMock()
            mock_session.post.return_value = mock_response
            mock_get_session.return_value = mock_session
            result = connector.generate("prompt", "context")
            mock_get_session.assert_called_once()
            mock_session.post.assert_called_once()

    def test_generate_with_tools_uses_session(self):
        connector = self._make_connector()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hello"}}]
        }
        with patch.object(connector, '_get_session') as mock_get_session:
            mock_session = MagicMock()
            mock_session.post.return_value = mock_response
            mock_get_session.return_value = mock_session
            result = connector.generate_with_tools([{"role": "user", "content": "hi"}])
            mock_get_session.assert_called_once()
            mock_session.post.assert_called_once()


if __name__ == '__main__':
    unittest.main()