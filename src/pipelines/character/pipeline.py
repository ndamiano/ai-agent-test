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
                schema={
                    "type": "object",
                    "properties": {
                        "name":            {"type": "string"},
                        "role":            {"type": "string"},
                        "archetype":       {"type": "string"},
                        "backstory_hook":  {"type": "string"},
                        "goal":            {"type": "string"},
                        "flaw":            {"type": "string"},
                        "setting_context": {"type": "string"},
                    },
                    "required": ["name", "role", "archetype", "backstory_hook", "goal", "flaw", "setting_context"],
                },
            ),
        ]),

        Node("character", [
            FnStage(
                id="character",
                fn=generate_character,
                inputs=["brief.json", "concept.json"],
                output="character.json",
                prompt_file="identity.txt",
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
