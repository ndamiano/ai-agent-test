"""Tests for the character creation pipeline."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipelines.character.fns import generate_character, generate_portrait, _json_with_correction, _SYSTEM


def _mock_agent(responses):
    mock = MagicMock()
    mock.send.side_effect = responses
    return mock


class TestJsonWithCorrection:
    def test_returns_on_first_try(self):
        agent = _mock_agent(['{"key": "val"}'])
        assert _json_with_correction(agent, "p", "t") == {"key": "val"}
        assert agent.send.call_count == 1

    def test_retries_on_bad_json(self):
        agent = _mock_agent(["bad", '{"key": "val"}'])
        assert _json_with_correction(agent, "p", "t") == {"key": "val"}
        assert agent.send.call_count == 2

    def test_raises_after_max_retries(self):
        agent = _mock_agent(["bad", "bad", "bad"])
        with pytest.raises(RuntimeError, match="Failed to get valid JSON"):
            _json_with_correction(agent, "p", "label")


class TestGenerateCharacter:
    def _make_inputs(self):
        return {
            "brief": {"concept": "a disgraced knight", "tone": "dark", "setting": "medieval"},
            "concept": {
                "name": "Sir Roland",
                "role": "anti-hero",
                "archetype": "fallen",
                "backstory_hook": "Lost his honor at the Siege of Ashenvale.",
                "goal": "Reclaim his family name",
                "flaw": "Cannot let go of pride",
                "setting_context": "Wandering sellsword, former knight of the crown",
            },
        }

    def test_makes_three_calls(self, tmp_path):
        identity   = {"id": "sir_roland", "name": "Sir Roland", "role": "anti-hero", "description": "d", "personality": ["proud"], "motivation": "m", "conflict": "c"}
        appearance = {"appearance": "Weathered face, scarred cheek, battered plate armour."}
        voice      = {"speech_patterns": "Formal. Never uses contractions. Pauses before answering."}

        with patch("pipelines.character.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.side_effect = [json.dumps(identity), json.dumps(appearance), json.dumps(voice)]
            MockAgent.return_value = mock_agent

            result = generate_character(self._make_inputs(), tmp_path)

        assert mock_agent.send.call_count == 3
        MockAgent.assert_called_once_with(_SYSTEM)

    def test_merges_all_field_groups(self, tmp_path):
        identity   = {"id": "sir_roland", "name": "Sir Roland", "role": "anti-hero", "description": "d", "personality": ["proud"], "motivation": "m", "conflict": "c"}
        appearance = {"appearance": "Weathered."}
        voice      = {"speech_patterns": "Formal."}

        with patch("pipelines.character.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.side_effect = [json.dumps(identity), json.dumps(appearance), json.dumps(voice)]
            MockAgent.return_value = mock_agent

            result = generate_character(self._make_inputs(), tmp_path)

        assert result["id"] == "sir_roland"
        assert result["appearance"] == "Weathered."
        assert result["speech_patterns"] == "Formal."


class TestGeneratePortrait:
    def test_saves_on_success(self, tmp_path):
        character_inputs = {"character": {"name": "Sir Roland", "appearance": "Weathered face."}}

        fake_src = tmp_path / "generated.png"
        fake_src.write_bytes(b"PNG")

        with patch("tools.comfyui_tools.generate_image") as mock_gen:
            mock_gen.return_value = {"success": True, "saved_paths": [str(fake_src)]}
            result = generate_portrait(character_inputs, tmp_path)

        assert result["status"] == "ok"
        assert Path(result["file"]).exists()

    def test_writes_placeholder_on_failure(self, tmp_path):
        character_inputs = {"character": {"name": "Sir Roland", "appearance": "Weathered face."}}

        with patch("tools.comfyui_tools.generate_image") as mock_gen:
            mock_gen.return_value = {"success": False, "error": "ComfyUI unavailable"}
            result = generate_portrait(character_inputs, tmp_path)

        assert result["status"] == "placeholder"
        assert Path(result["file"]).exists()


class TestCharacterPipelineRegistry:
    def test_character_registered(self):
        from pipelines.registry import get_registry
        registry = get_registry()
        assert "character" in registry

    def test_brief_schema_requires_concept(self):
        from pipelines.registry import get_registry
        defn = get_registry()["character"]
        assert "concept" in defn.brief_schema["required"]

    def test_enrich_sets_defaults(self):
        from pipelines.registry import get_registry
        defn = get_registry()["character"]
        enriched = defn.enrich_brief({"concept": "a rogue"})
        assert enriched["tone"] == "balanced — neither too dark nor too light"
        assert enriched["concept"] == "a rogue"

    def test_enrich_user_values_override_defaults(self):
        from pipelines.registry import get_registry
        defn = get_registry()["character"]
        enriched = defn.enrich_brief({"concept": "a rogue", "tone": "grim"})
        assert enriched["tone"] == "grim"
