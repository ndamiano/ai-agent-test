from pathlib import Path

from pipelines.runner import Pipeline, Node, FnStage
from pipelines.renpy.fns import (
    generate_bible,
    generate_scene_plan,
    generate_asset_manifest,
    write_scene_scripts,
    continuity_pass,
    generate_images,
    build,
)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

RENPY_V3_PIPELINE = Pipeline(
    name="renpy",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("bible", [
            FnStage(
                id="bible",
                fn=generate_bible,
                inputs=["brief.json"],
                output="bible.json",
                prompt_file="bible.txt",
            ),
        ]),

        Node("scene_plan", [
            FnStage(
                id="scene_plan",
                fn=generate_scene_plan,
                inputs=["brief.json", "bible.json"],
                output="scene_plan.json",
                prompt_file="scene_plan.txt",
                max_tokens=8000,
            ),
        ]),

        Node("asset_manifest", [
            FnStage(
                id="asset_manifest",
                fn=generate_asset_manifest,
                inputs=["bible.json", "scene_plan.json"],
                output="asset_manifest.json",
                prompt_file="asset_manifest.txt",
            ),
        ]),

        Node("scene_scripts", [
            FnStage(
                id="scene_scripts",
                fn=write_scene_scripts,
                inputs=["brief.json", "bible.json", "scene_plan.json", "asset_manifest.json"],
                output="scene_scripts.json",
                prompt_file="scene_script.txt",
            ),
        ]),

        Node("continuity", [
            FnStage(
                id="continuity",
                fn=continuity_pass,
                inputs=["bible.json", "scene_scripts.json"],
                output="scene_scripts_revised.json",
                prompt_file="continuity_check.txt",
            ),
        ]),

        Node("images", [
            FnStage(
                id="images",
                fn=generate_images,
                inputs=["bible.json", "asset_manifest.json"],
                output="images_result.json",
            ),
        ]),

        Node("build", [
            FnStage(
                id="build",
                fn=build,
                inputs=["brief.json", "bible.json", "asset_manifest.json", "scene_scripts_revised.json"],
                output="build_result.json",
            ),
        ]),

    ],
)
