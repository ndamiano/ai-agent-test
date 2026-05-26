"""Tests for character generation decomposition."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from pipelines.renpy.fns import dialogue, generate_characters, _generate_dialogue_scene, _json_with_correction, _SYSTEM as _CHARACTER_SYSTEM


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
    def test_calls_character_subpipeline(self, minimal_inputs, tmp_path):
        sub_result = {
            "status": "completed",
            "outputs": {
                "character": {
                    "id": "aria",
                    "name": "Aria",
                    "description": "A pilot.",
                    "role": "protagonist",
                    "personality": ["brave"],
                    "appearance": "Tall, dark hair, flight suit.",
                    "speech_patterns": "Clipped sentences. Never says please.",
                },
                "portrait_result": {"status": "ok", "file": str(tmp_path / "aria.png")},
            },
        }

        with patch("pipelines.registry.run_subpipeline", return_value=sub_result) as mock_run:
            result = generate_characters(minimal_inputs, tmp_path)

        mock_run.assert_called_once()
        assert mock_run.call_args.args[:2] == (tmp_path, "character")
        assert result["characters"][0]["id"] == "aria"
        assert result["characters"][0]["portrait_file"] == str(tmp_path / "aria.png")

    def test_normalizes_subpipeline_character_for_renpy_contract(self, minimal_inputs, tmp_path):
        sub_result = {
            "status": "completed",
            "outputs": {
                "character": {
                    "id": "aria",
                    "name": "Aria",
                    "description": "A pilot.",
                    "role": "protagonist",
                    "personality": ["brave"],
                    "appearance": "Tall, dark hair.",
                    "speech_patterns": "Clipped sentences.",
                },
            },
        }

        with patch("pipelines.registry.run_subpipeline", return_value=sub_result):
            result = generate_characters(minimal_inputs, tmp_path)

        chars = result["characters"]
        assert len(chars) == 1
        char = chars[0]
        assert char["id"] == "aria"
        assert char["appearance"] == "Tall, dark hair."
        assert char["speech_patterns"] == "Clipped sentences."
        assert char["color"].startswith("#")

    def test_calls_subpipeline_per_character(self, tmp_path):
        inputs = {
            "brief": {"genre": "sci-fi", "tone": "dark", "notes": "test", "character_count": "2"},
            "story": {"arc": "arc", "premise": "premise", "story_beats": []},
            "settings": [],
        }
        results = [
            {"status": "completed", "outputs": {"character": {"id": "c", "name": "C", "description": "d", "role": "supporting", "personality": [], "appearance": "a", "speech_patterns": "v"}}},
            {"status": "completed", "outputs": {"character": {"id": "c2", "name": "C2", "description": "d2", "role": "supporting", "personality": [], "appearance": "a2", "speech_patterns": "v2"}}},
        ]

        with patch("pipelines.registry.run_subpipeline", side_effect=results) as mock_run:
            result = generate_characters(inputs, tmp_path)

        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0].kwargs["run_id"] == "character_1_attempt1"
        assert mock_run.call_args_list[1].kwargs["run_id"] == "character_2_attempt1"
        assert len(result["characters"]) == 2


class TestDialogueGeneration:
    def _make_scene_inputs(self, n_lines="2", characters=None):
        return {
            "genre": "romance",
            "tone": "warm",
            "lines_per_scene": n_lines,
            "characters": characters or [
                {"id": "elias", "name": "Elias", "description": "quiet man", "personality": ["reserved"], "speech_patterns": "speaks slowly"},
                {"id": "yui",   "name": "Yui",   "description": "sharp woman", "personality": ["direct"], "speech_patterns": "clipped sentences"},
            ],
            "setting": {"id": "s1", "name": "Station", "description": "old platform"},
            "scene": {"id": "scene_1", "setting_id": "s1", "summary": "reunion", "what_changes": "they reconnect", "revelation": None},
        }

    def test_produces_correct_number_of_lines(self):
        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            MockAgent.return_value.send.return_value = "Hello."
            result = _generate_dialogue_scene(self._make_scene_inputs("4"), "s1")
        assert len(result["lines"]) == 4

    def test_alternates_characters(self):
        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            MockAgent.return_value.send.return_value = "Hello."
            result = _generate_dialogue_scene(self._make_scene_inputs("4"), "s1")
        ids = [l["character_id"] for l in result["lines"]]
        assert ids == ["elias", "yui", "elias", "yui"]

    def test_strips_name_prefix(self):
        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            MockAgent.return_value.send.return_value = "Elias: I remember this place."
            result = _generate_dialogue_scene(self._make_scene_inputs("1"), "s1")
        assert result["lines"][0]["text"] == "I remember this place."

    def test_output_format(self):
        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            MockAgent.return_value.send.return_value = "A line."
            result = _generate_dialogue_scene(self._make_scene_inputs("2"), "s1")
        assert result["scene_id"] == "scene_1"
        assert result["setting_id"] == "s1"
        for line in result["lines"]:
            assert set(line.keys()) == {"character_id", "text"}

    def test_dialogue_processes_all_scenes(self, tmp_path):
        inputs = {
            "brief": {"genre": "romance", "tone": "warm", "lines_per_scene": "2"},
            "characters": {"characters": [
                {"id": "elias", "name": "Elias", "description": "d", "personality": [], "speech_patterns": "quiet"},
            ]},
            "settings": {"settings": [
                {"id": "station", "name": "Station", "description": "old platform"},
            ]},
            "scenes": {"scenes": [
                {"id": "scene_1", "title": "Arrival", "setting_id": "station", "character_ids": ["elias"]},
                {"id": "scene_2", "title": "Cafe",    "setting_id": "station", "character_ids": ["elias"]},
            ]},
        }
        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            MockAgent.return_value.send.return_value = "A line."
            result = dialogue(inputs, tmp_path)
        assert [s["scene_id"] for s in result["dialogue_scenes"]] == ["scene_1", "scene_2"]
