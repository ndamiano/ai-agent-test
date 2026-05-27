"""Tests for the character creation pipeline."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipelines.character.fns import (
    generate_identity, generate_appearance, generate_voice,
    assemble_character, generate_portrait, _json_with_correction,
)


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


class TestGenerateIdentity:
    def _make_inputs(self):
        return {
            "brief": {"tone": "dark", "setting": "medieval"},
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

    def test_makes_one_call(self, tmp_path):
        identity = {"id": "sir_roland", "name": "Sir Roland", "role": "anti-hero",
                    "description": "d", "personality": ["proud"], "motivation": "m", "conflict": "c"}

        with patch("pipelines.character.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.return_value = json.dumps(identity)
            MockAgent.return_value = mock_agent

            result = generate_identity(self._make_inputs(), tmp_path)

        assert mock_agent.send.call_count == 1
        assert result["id"] == "sir_roland"
        assert result["name"] == "Sir Roland"


class TestGenerateAppearance:
    def _make_inputs(self):
        return {
            "brief": {"tone": "dark", "setting": "medieval"},
            "identity": {
                "name": "Sir Roland",
                "description": "A disgraced knight.",
                "personality": ["proud", "bitter"],
                "motivation": "Reclaim honor",
                "conflict": "Cannot let go of pride",
            },
        }

    def test_makes_one_call_with_identity_context(self, tmp_path):
        appearance = {"appearance": "Weathered face, scarred cheek, battered plate armour."}

        with patch("pipelines.character.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.return_value = json.dumps(appearance)
            MockAgent.return_value = mock_agent

            result = generate_appearance(self._make_inputs(), tmp_path)

        assert mock_agent.send.call_count == 1
        assert result["appearance"] == "Weathered face, scarred cheek, battered plate armour."

    def test_passes_identity_to_prompt(self, tmp_path):
        appearance = {"appearance": "Tall."}

        with patch("pipelines.character.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.return_value = json.dumps(appearance)
            MockAgent.return_value = mock_agent

            generate_appearance(self._make_inputs(), tmp_path)

        prompt_sent = mock_agent.send.call_args[0][0]
        assert "Sir Roland" in prompt_sent
        assert "dark" in prompt_sent  # tone


class TestGenerateVoice:
    def _make_inputs(self):
        return {
            "brief": {"tone": "dark"},
            "identity": {
                "name": "Sir Roland",
                "description": "A disgraced knight.",
                "personality": ["proud", "bitter"],
                "motivation": "Reclaim honor",
                "conflict": "Cannot let go of pride",
            },
        }

    def test_makes_one_call_with_identity_context(self, tmp_path):
        voice = {"speech_patterns": "Formal. Never uses contractions. Pauses before answering."}

        with patch("pipelines.character.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.return_value = json.dumps(voice)
            MockAgent.return_value = mock_agent

            result = generate_voice(self._make_inputs(), tmp_path)

        assert mock_agent.send.call_count == 1
        assert result["speech_patterns"] == "Formal. Never uses contractions. Pauses before answering."

    def test_passes_identity_to_prompt(self, tmp_path):
        voice = {"speech_patterns": "Formal."}

        with patch("pipelines.character.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.return_value = json.dumps(voice)
            MockAgent.return_value = mock_agent

            generate_voice(self._make_inputs(), tmp_path)

        prompt_sent = mock_agent.send.call_args[0][0]
        assert "Sir Roland" in prompt_sent
        assert "Reclaim honor" in prompt_sent


class TestAssembleCharacter:
    def test_merges_all_fields(self, tmp_path):
        import json as _json
        identity = {"id": "sir_roland", "name": "Sir Roland", "role": "anti-hero",
                    "description": "d", "personality": ["proud"], "motivation": "m", "conflict": "c"}
        (tmp_path / "appearance.json").write_text(_json.dumps({"appearance": "Weathered."}))
        (tmp_path / "voice.json").write_text(_json.dumps({"speech_patterns": "Formal."}))
        (tmp_path / "examples.json").write_text(_json.dumps({"example_dialogue": []}))
        inputs = {"identity": identity}
        result = assemble_character(inputs, tmp_path)
        assert result["id"] == "sir_roland"
        assert result["appearance"] == "Weathered."
        assert result["speech_patterns"] == "Formal."

    def test_later_fields_overwrite_earlier(self, tmp_path):
        import json as _json
        (tmp_path / "appearance.json").write_text(_json.dumps({"name": "B"}))
        (tmp_path / "voice.json").write_text(_json.dumps({}))
        (tmp_path / "examples.json").write_text(_json.dumps({}))
        inputs = {"identity": {"name": "A"}}
        result = assemble_character(inputs, tmp_path)
        assert result["name"] == "B"


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
