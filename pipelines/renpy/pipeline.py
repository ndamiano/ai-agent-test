from pathlib import Path

from engine.pipeline_runner import Pipeline, Node, LLMStage, FnStage
from pipelines.renpy.fns import dialogue, package, build

_PROMPTS_DIR = Path(__file__).parent / "prompts"

RENPY_PIPELINE = Pipeline(
    name="renpy",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("setup", [
            LLMStage(
                id="characters",
                prompt_template="characters.txt",
                output="characters.json",
                schema={"required": ["characters"]},
            ),
            LLMStage(
                id="settings",
                prompt_template="settings.txt",
                output="settings.json",
                schema={"required": ["settings"]},
            ),
        ]),

        Node("scenes", [
            LLMStage(
                id="scenes",
                prompt_template="scenes.txt",
                output="scenes.json",
                schema={"required": ["scenes"]},
            ),
        ]),

        Node("dialogue", [
            FnStage(
                id="dialogue",
                fn=dialogue,
                inputs=["brief.json", "characters.json", "scenes.json"],
                output="dialogue.json",
            ),
        ]),

        Node("package", [
            FnStage(
                id="package",
                fn=package,
                inputs=["brief.json", "characters.json", "settings.json", "dialogue.json"],
                output="game_definition.json",
            ),
        ]),

        Node("build", [
            FnStage(
                id="build",
                fn=build,
                inputs=["game_definition.json"],
                output="build_result.json",
            ),
        ]),

    ],
)
