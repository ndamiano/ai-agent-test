from pathlib import Path

from pipelines.runner import Pipeline, Node, FnStage
from pipelines.renpy.fns import (
    generate_story_arc, generate_story_beats, generate_story_locations, assemble_story,
    generate_settings, generate_characters, generate_scenes, dialogue,
    package, generate_images, build,
)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

RENPY_PIPELINE = Pipeline(
    name="renpy",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("story_arc", [
            FnStage(
                id="story_arc",
                fn=generate_story_arc,
                inputs=["brief.json"],
                output="story_arc.json",
                prompt_file="story_arc.txt",
            ),
        ]),

        Node("story_beats", [
            FnStage(
                id="story_beats",
                fn=generate_story_beats,
                inputs=["brief.json", "story_arc.json"],
                output="story_beats.json",
                prompt_file="story_beat.txt",
            ),
        ]),

        Node("story_locations", [
            FnStage(
                id="story_locations",
                fn=generate_story_locations,
                inputs=["brief.json", "story_arc.json", "story_beats.json"],
                output="story_locations.json",
                prompt_file="story_locations.txt",
            ),
        ]),

        Node("story", [
            FnStage(
                id="story",
                fn=assemble_story,
                inputs=["story_arc.json", "story_beats.json", "story_locations.json"],
                output="story.json",
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
                prompt_file="dialogue_turn.txt",
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
