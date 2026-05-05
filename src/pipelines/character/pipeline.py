from pathlib import Path

from pipelines.runner import Pipeline, Node, LLMStage, FnStage
from pipelines.character.fns import generate_character, generate_portrait

_PROMPTS_DIR = Path(__file__).parent / "prompts"

CHARACTER_PIPELINE = Pipeline(
    name="character",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("concept", [
            LLMStage(
                id="concept",
                prompt_template="concept.txt",
                output="concept.json",
                schema={"required": ["name", "role", "backstory_hook", "goal", "flaw"]},
            ),
        ]),

        Node("character", [
            FnStage(
                id="character",
                fn=generate_character,
                inputs=["brief.json", "concept.json"],
                output="character.json",
            ),
        ]),

        Node("portrait", [
            FnStage(
                id="portrait",
                fn=generate_portrait,
                inputs=["character.json"],
                output="portrait_result.json",
            ),
        ]),

    ],
)
