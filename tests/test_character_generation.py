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
    def test_retries_dialogue_scene_with_fresh_agent(self):
        scene_inputs = {
            "genre": "romance",
            "tone": "warm",
            "lines_per_scene": "2",
            "characters": [{"id": "elias", "name": "Elias"}],
            "setting": {"id": "s1", "name": "Station"},
            "scene": {"id": "scene_1", "setting_id": "s1", "what_changes": "They reconnect."},
        }

        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            bad_agent = MagicMock()
            bad_agent.send.side_effect = ["not json", "still not json"]
            good_agent = MagicMock()
            good_agent.send.return_value = '{"lines": [{"character_id": null, "text": "The platform clock clicked."}, {"character_id": "elias", "text": "I remember this place."}]}'
            MockAgent.side_effect = [bad_agent, good_agent]

            result = _generate_dialogue_scene(scene_inputs, "Scene 1", max_attempts=2)

        assert result["scene_id"] == "scene_1"
        assert len(result["lines"]) == 2
        assert MockAgent.call_count == 2

    def test_dialogue_preserves_successful_scenes_when_later_scene_retries(self, tmp_path):
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
                {"id": "scene_2", "title": "Cafe", "setting_id": "station", "character_ids": ["elias"]},
            ]},
        }

        with patch("pipelines.renpy.fns.PipelineAgent") as MockAgent:
            scene_1 = MagicMock()
            scene_1.send.return_value = '{"lines": [{"character_id": null, "text": "arrival"}, {"character_id": "elias", "text": "home"}]}'
            scene_2_bad = MagicMock()
            scene_2_bad.send.side_effect = ["bad", "bad"]
            scene_2_good = MagicMock()
            scene_2_good.send.return_value = '{"lines": [{"character_id": null, "text": "cafe"}, {"character_id": "elias", "text": "coffee"}]}'
            MockAgent.side_effect = [scene_1, scene_2_bad, scene_2_good]

            result = dialogue(inputs, tmp_path)

        assert [s["scene_id"] for s in result["dialogue_scenes"]] == ["scene_1", "scene_2"]
