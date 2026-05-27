"""Tests for the TTRPG campaign pipeline."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


from pipelines.ttrpg.fns import generate_npcs, assemble_document


def _mock_agent(responses):
    mock = MagicMock()
    mock.send.side_effect = responses
    return mock


_SETTING = {
    "name": "The Shattered Reach",
    "overview": "A fractured kingdom where three factions fight over the ruins of a collapsed empire.",
    "magic_tech_level": "Low magic. Relics from the old empire still function, but no one knows how.",
    "key_tensions": ["The throne is empty", "Ancient weapons are waking up", "Food is running out"],
    "tone_notes": "Gritty. Nobody wins cleanly.",
    "starting_location": {
        "name": "Ashport",
        "description": "A harbour town choking on refugees.",
        "hooks": ["A body in the well", "Soldiers demanding papers"],
    },
}

_FACTIONS = [
    {"id": "iron_council", "name": "Iron Council", "goal": "Restore the old order", "method": "Political manipulation", "strength": "Deep treasury", "weakness": "Internal power struggle", "relationship_to_players": "neutral"},
    {"id": "free_cities", "name": "Free Cities", "goal": "Prevent any single ruler", "method": "Assassination and sabotage", "strength": "Spy network", "weakness": "No army", "relationship_to_players": "ally"},
]


class TestGenerateNpcs:
    def _make_inputs(self, npc_count=2):
        return {
            "brief": {"title": "Shattered Reach", "genre": "dark fantasy", "tone": "gritty", "npc_count": str(npc_count)},
            "setting": _SETTING,
            "factions": {"factions": _FACTIONS},
        }

    def _npc(self, idx):
        return {
            "id": f"npc_{idx}",
            "name": f"NPC {idx}",
            "faction_id": "iron_council",
            "role": "quest giver",
            "description": "A tired magistrate.",
            "personality": ["weary", "principled"],
            "motivation": "Keep the town alive",
            "secret": "Has been skimming taxes",
            "dialogue_hook": "Sit down. We don't have much time.",
        }

    def test_generates_correct_count(self, tmp_path):
        responses = [json.dumps(self._npc(i)) for i in range(2)]

        with patch("pipelines.ttrpg.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.side_effect = responses
            MockAgent.return_value = mock_agent

            result = generate_npcs(self._make_inputs(npc_count=2), tmp_path)

        assert len(result["npcs"]) == 2

    def test_creates_fresh_agent_per_npc(self, tmp_path):
        responses = [json.dumps(self._npc(i)) for i in range(3)]

        with patch("pipelines.ttrpg.fns.PipelineAgent") as MockAgent:
            mock_agent = MagicMock()
            mock_agent.send.side_effect = responses
            MockAgent.return_value = mock_agent

            generate_npcs(self._make_inputs(npc_count=3), tmp_path)

        assert MockAgent.call_count == 3

    def test_passes_existing_npcs_as_context(self, tmp_path):
        npc1 = self._npc(1)
        npc2 = self._npc(2)

        with patch("pipelines.ttrpg.fns.PipelineAgent") as MockAgent:
            with patch("pipelines.ttrpg.fns.render_template") as mock_render:
                mock_render.return_value = "prompt"
                mock_agent = MagicMock()
                mock_agent.send.side_effect = [json.dumps(npc1), json.dumps(npc2)]
                MockAgent.return_value = mock_agent

                generate_npcs(self._make_inputs(npc_count=2), tmp_path)

                _, second_ctx = mock_render.call_args_list[1][0]
                assert len(second_ctx["existing_npcs"]) == 1
                assert second_ctx["existing_npcs"][0]["id"] == "npc_1"


class TestAssembleDocument:
    def _make_inputs(self):
        return {
            "brief": {"title": "Shattered Reach", "genre": "dark fantasy", "tone": "gritty"},
            "setting": _SETTING,
            "factions": {"factions": _FACTIONS},
            "npcs": {"npcs": [
                {"id": "marta", "name": "Marta", "role": "quest giver", "description": "A tired magistrate.", "motivation": "Keep order", "secret": "Embezzling", "dialogue_hook": "Sit down."},
            ]},
            "encounters": {"encounters": [
                {"id": "well_body", "title": "Body in the Well", "type": "social", "setup": "Crowd around the town well.", "stakes": "Public trust", "twist": "The body is the mayor."},
            ]},
            "plot_hooks": {
                "main_quest": {
                    "title": "The Empty Throne",
                    "inciting_incident": "Players witness an assassination attempt on a candidate.",
                    "goal": "Prevent civil war",
                    "obstacles": ["The Iron Council wants war", "A relic weapon is missing", "Marta knows too much"],
                    "climax": "Players must choose who sits the throne.",
                    "stakes": "The kingdom fractures permanently.",
                },
                "side_quests": [
                    {"title": "The Missing Shipment", "hook": "Merchant asks for help", "goal": "Find the grain", "reward": "Gold + faction favour", "faction_id": "free_cities"},
                ],
                "random_hooks": ["A stranger collapses in the street clutching a map", "Two guards argue over a locked chest"],
            },
        }

    def test_writes_markdown_file(self, tmp_path):
        result = assemble_document(self._make_inputs(), tmp_path)
        assert result["status"] == "ok"
        doc_path = Path(result["file"])
        assert doc_path.exists()
        assert doc_path.suffix == ".md"

    def test_document_contains_key_sections(self, tmp_path):
        result = assemble_document(self._make_inputs(), tmp_path)
        content = Path(result["file"]).read_text()
        assert "# Shattered Reach" in content
        assert "## The World" in content
        assert "## Factions" in content
        assert "## Notable NPCs" in content
        assert "## Encounters" in content
        assert "## Main Quest" in content
        assert "## Side Quests" in content

    def test_document_includes_npc_secret(self, tmp_path):
        result = assemble_document(self._make_inputs(), tmp_path)
        content = Path(result["file"]).read_text()
        assert "Embezzling" in content

    def test_title_from_brief(self, tmp_path):
        result = assemble_document(self._make_inputs(), tmp_path)
        assert result["title"] == "Shattered Reach"


class TestTtrpgPipelineRegistry:
    def test_ttrpg_registered(self):
        from pipelines.registry import get_registry
        assert "ttrpg" in get_registry()

    def test_brief_schema_required_fields(self):
        from pipelines.registry import get_registry
        defn = get_registry()["ttrpg"]
        for field in ["title", "genre", "tone", "premise"]:
            assert field in defn.brief_schema["required"]

    def test_enrich_sets_scale_defaults(self):
        from pipelines.registry import get_registry
        defn = get_registry()["ttrpg"]
        enriched = defn.enrich_brief({"title": "T", "genre": "fantasy", "tone": "dark", "premise": "p"})
        assert enriched["scale"] == "short"
        assert enriched["npc_count"] == "6"
        assert enriched["encounter_count"] == "5"

    def test_enrich_respects_scale_override(self):
        from pipelines.registry import get_registry
        defn = get_registry()["ttrpg"]
        enriched = defn.enrich_brief({"title": "T", "genre": "fantasy", "tone": "dark", "premise": "p", "scale": "campaign"})
        assert enriched["npc_count"] == "10"
        assert enriched["encounter_count"] == "8"

    def test_enrich_user_npc_count_wins(self):
        from pipelines.registry import get_registry
        defn = get_registry()["ttrpg"]
        enriched = defn.enrich_brief({"title": "T", "genre": "fantasy", "tone": "dark", "premise": "p", "npc_count": "15"})
        assert enriched["npc_count"] == "15"
