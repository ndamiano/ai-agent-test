"""Tests for character generation decomposition."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from pipelines.renpy.fns import generate_characters, _json_with_correction, _CHARACTER_SYSTEM


@pytest.fixture
def minimal_inputs():
    return {
        "brief": {"genre": "sci-fi", "tone": "dark", "notes": "test", "character_count": "1"},
        "story": {"arc": "test arc", "premise": "test premise", "story_beats": []},
        "settings": [{"id": "s1", "name": "Station", "description": "space station"}],
    }


def _make_agent_mock(responses):
    """Returns a mock PipelineAgent whose send() yields responses in order."""
    mock = MagicMock()
    mock.send.side_effect = responses
    return mock


class TestJsonWithCorrection:
    def test_returns_parsed_json_on_first_try(self):
        agent = _make_agent_mock(['{"key": "value"}'])
        result = _json_with_correction(agent, "prompt", "test")
        assert result == {"key": "value"}
        assert agent.send.call_count == 1

    def test_retries_on_invalid_json(self):
        agent = _make_agent_mock(["not json", '{"key": "value"}'])
        result = _json_with_correction(agent, "prompt", "test")
        assert result == {"key": "value"}
        assert agent.send.call_count == 2

    def test_raises_after_max_retries(self):
        agent = _make_agent_mock(["bad", "bad", "bad"])
        with pytest.raises(RuntimeError, match="Failed to get valid JSON"):
            _json_with_correction(agent, "prompt", "test label")

    def test_strips_fences_before_parsing(self):
        agent = _make_agent_mock(['```json\n{"key": "value"}\n```'])
        result = _json_with_correction(agent, "prompt", "test")
        assert result == {"key": "value"}


class TestGenerateCharacters:
    def test_makes_three_calls_per_character(self, minimal_inputs, tmp_path):
        identity = {"id": "aria", "name": "Aria", "color": "#ff0000", "description": "A pilot.", "role": "protagonist", "personality": ["brave"]}
        appearance = {"appearance": "Tall, dark hair, flight suit."}
        voice = {"speech_patterns": "Clipped sentences. Never says please."}

        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.side_effect = [
                json.dumps(identity),
                json.dumps(appearance),
                json.dumps(voice),
            ]
            MockAgent.return_value = mock_agent

            result = generate_characters(minimal_inputs, tmp_path)

        assert mock_agent.send.call_count == 3
        MockAgent.assert_called_once_with(_CHARACTER_SYSTEM)

    def test_merges_all_three_field_groups(self, minimal_inputs, tmp_path):
        identity = {"id": "aria", "name": "Aria", "color": "#ff0000", "description": "A pilot.", "role": "protagonist", "personality": ["brave"]}
        appearance = {"appearance": "Tall, dark hair."}
        voice = {"speech_patterns": "Clipped sentences."}

        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.side_effect = [
                json.dumps(identity),
                json.dumps(appearance),
                json.dumps(voice),
            ]
            MockAgent.return_value = mock_agent

            result = generate_characters(minimal_inputs, tmp_path)

        chars = result["characters"]
        assert len(chars) == 1
        char = chars[0]
        assert char["id"] == "aria"
        assert char["appearance"] == "Tall, dark hair."
        assert char["speech_patterns"] == "Clipped sentences."

    def test_creates_fresh_agent_per_character(self, tmp_path):
        inputs = {
            "brief": {"genre": "sci-fi", "tone": "dark", "notes": "test", "character_count": "2"},
            "story": {"arc": "arc", "premise": "premise", "story_beats": []},
            "settings": [],
        }
        identity = {"id": "c", "name": "C", "color": "#fff", "description": "d", "role": "supporting", "personality": []}
        appearance = {"appearance": "a"}
        voice = {"speech_patterns": "v"}

        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.side_effect = [
                json.dumps(identity), json.dumps(appearance), json.dumps(voice),
                json.dumps({**identity, "id": "c2", "name": "C2"}), json.dumps(appearance), json.dumps(voice),
            ]
            MockAgent.return_value = mock_agent

            result = generate_characters(inputs, tmp_path)

        assert MockAgent.call_count == 2
        assert len(result["characters"]) == 2
