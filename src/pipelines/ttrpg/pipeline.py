from pathlib import Path

from pipelines.runner import Pipeline, Node, LLMStage, FnStage
from pipelines.ttrpg.fns import generate_npcs, assemble_document

_PROMPTS_DIR = Path(__file__).parent / "prompts"

TTRPG_PIPELINE = Pipeline(
    name="ttrpg",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("setting", [
            LLMStage(
                id="setting",
                prompt_template="setting.txt",
                output="setting.json",
                schema={
                    "type": "object",
                    "properties": {
                        "name":             {"type": "string"},
                        "overview":         {"type": "string"},
                        "magic_tech_level": {"type": "string"},
                        "key_tensions":     {"type": "array", "items": {"type": "string"}},
                        "tone_notes":       {"type": "string"},
                        "starting_location": {
                            "type": "object",
                            "properties": {
                                "name":        {"type": "string"},
                                "description": {"type": "string"},
                                "hooks":       {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["name", "description", "hooks"],
                        },
                    },
                    "required": ["name", "overview", "key_tensions", "starting_location"],
                },
            ),
        ]),

        Node("factions", [
            LLMStage(
                id="factions",
                prompt_template="factions.txt",
                output="factions.json",
                schema={
                    "type": "object",
                    "properties": {
                        "factions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id":                      {"type": "string"},
                                    "name":                    {"type": "string"},
                                    "goal":                    {"type": "string"},
                                    "method":                  {"type": "string"},
                                    "strength":                {"type": "string"},
                                    "weakness":                {"type": "string"},
                                    "relationship_to_players": {"type": "string"},
                                },
                                "required": ["id", "name", "goal", "method", "strength", "weakness", "relationship_to_players"],
                            },
                        },
                    },
                    "required": ["factions"],
                },
            ),
        ]),

        Node("npcs", [
            FnStage(
                id="npcs",
                fn=generate_npcs,
                inputs=["brief.json", "setting.json", "factions.json"],
                output="npcs.json",
            ),
        ]),

        Node("encounters", [
            LLMStage(
                id="encounters",
                prompt_template="encounters.txt",
                output="encounters.json",
                schema={
                    "type": "object",
                    "properties": {
                        "encounters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id":         {"type": "string"},
                                    "title":      {"type": "string"},
                                    "type":       {"type": "string"},
                                    "setup":      {"type": "string"},
                                    "stakes":     {"type": "string"},
                                    "twist":      {"type": "string"},
                                    "faction_id": {"type": ["string", "null"]},
                                    "npc_ids":    {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["id", "title", "type", "setup", "stakes", "twist"],
                            },
                        },
                    },
                    "required": ["encounters"],
                },
            ),
        ]),

        Node("plot_hooks", [
            LLMStage(
                id="plot_hooks",
                prompt_template="plot_hooks.txt",
                output="plot_hooks.json",
                schema={
                    "type": "object",
                    "properties": {
                        "main_quest": {
                            "type": "object",
                            "properties": {
                                "title":             {"type": "string"},
                                "inciting_incident": {"type": "string"},
                                "goal":              {"type": "string"},
                                "obstacles":         {"type": "array", "items": {"type": "string"}},
                                "climax":            {"type": "string"},
                                "stakes":            {"type": "string"},
                            },
                            "required": ["title", "inciting_incident", "goal", "obstacles", "climax", "stakes"],
                        },
                        "side_quests": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title":      {"type": "string"},
                                    "hook":       {"type": "string"},
                                    "goal":       {"type": "string"},
                                    "reward":     {"type": "string"},
                                    "faction_id": {"type": ["string", "null"]},
                                },
                                "required": ["title", "hook", "goal", "reward"],
                            },
                        },
                        "random_hooks": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["main_quest", "side_quests", "random_hooks"],
                },
            ),
        ]),

        Node("document", [
            FnStage(
                id="document",
                fn=assemble_document,
                inputs=["brief.json", "setting.json", "factions.json", "npcs.json", "encounters.json", "plot_hooks.json"],
                output="document_result.json",
            ),
        ]),

    ],
)
