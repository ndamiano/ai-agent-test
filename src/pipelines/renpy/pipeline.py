from pathlib import Path

from pipelines.runner import Pipeline, Node, LLMStage, FnStage
from pipelines.renpy.fns import dialogue, generate_characters, generate_scenes, package, generate_images, build

_PROMPTS_DIR = Path(__file__).parent / "prompts"

RENPY_PIPELINE = Pipeline(
    name="renpy",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("story", [
            LLMStage(
                id="story",
                prompt_template="story.txt",
                output="story.json",
                schema={"required": ["arc", "story_beats", "location_needs"]},
            ),
        ]),

        Node("settings", [
            LLMStage(
                id="settings",
                prompt_template="settings.txt",
                output="settings.json",
                schema={"required": ["settings"]},
            ),
        ]),

        Node("characters", [
            FnStage(
                id="characters",
                fn=generate_characters,
                inputs=["brief.json", "story.json", "settings.json"],
                output="characters.json",
            ),
        ]),

        Node("scenes", [
            FnStage(
                id="scenes",
                fn=generate_scenes,
                inputs=["brief.json", "story.json", "settings.json", "characters.json"],
                output="scenes.json",
            ),
        ]),

        Node("dialogue", [
            FnStage(
                id="dialogue",
                fn=dialogue,
                inputs=["brief.json", "characters.json", "settings.json", "scenes.json"],
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

        Node("images", [
            FnStage(
                id="images",
                fn=generate_images,
                inputs=["game_definition.json", "settings.json", "characters.json"],
                output="images_result.json",
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
