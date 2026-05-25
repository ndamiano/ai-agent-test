from pathlib import Path

from pipelines.runner import Pipeline, Node, FnStage
from pipelines.renpy.fns import dialogue, generate_characters, generate_scenes, generate_settings, generate_story, package, generate_images, build

_PROMPTS_DIR = Path(__file__).parent / "prompts"

RENPY_PIPELINE = Pipeline(
    name="renpy",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("story", [
            FnStage(
                id="story",
                fn=generate_story,
                inputs=["brief.json"],
                output="story.json",
                prompt_file="story_beat.txt",
            ),
        ]),

        Node("settings", [
            FnStage(
                id="settings",
                fn=generate_settings,
                inputs=["brief.json", "story.json"],
                output="settings.json",
                prompt_file="settings.txt",
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
                prompt_file="scene.txt",
                max_tokens=8000,
            ),
        ]),

        Node("dialogue", [
            FnStage(
                id="dialogue",
                fn=dialogue,
                inputs=["brief.json", "characters.json", "settings.json", "scenes.json"],
                output="dialogue.json",
                prompt_file="dialogue.txt",
                max_tokens=25000,
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
