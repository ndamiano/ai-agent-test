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
                schema={"required": ["name", "overview", "key_tensions", "starting_location"]},
            ),
        ]),

        Node("factions", [
            LLMStage(
                id="factions",
                prompt_template="factions.txt",
                output="factions.json",
                schema={"required": ["factions"]},
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
                schema={"required": ["encounters"]},
            ),
        ]),

        Node("plot_hooks", [
            LLMStage(
                id="plot_hooks",
                prompt_template="plot_hooks.txt",
                output="plot_hooks.json",
                schema={"required": ["main_quest", "side_quests"]},
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
