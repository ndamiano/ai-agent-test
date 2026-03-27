# tests/test_connection_pooling.py
"""Verify that all HTTP methods use _get_session() for connection pooling."""

import unittest
from unittest.mock import patch, MagicMock
from llm_clients.openai_compatible_connector import OpenAICompatibleConnector


class TestClineConnectionPooling(unittest.TestCase):
    """Test that OpenAICompatibleConnector (Cline config) uses _get_session() consistently."""

    def _make_connector(self):
        connector = OpenAICompatibleConnector(
            base_url="https://api.cline.bot/api",
            api_key="test-key",
            model="test-model",
            temperature=0.7,
            max_tokens=100
        )
        connector._connected = True
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
    """Test that OpenAICompatibleConnector (LMStudio config) uses _get_session() consistently."""

    def _make_connector(self):
        connector = OpenAICompatibleConnector(
            base_url="http://localhost:1234",
            model="test-model",
            temperature=0.7,
            max_tokens=100
        )
        connector._connected = True
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