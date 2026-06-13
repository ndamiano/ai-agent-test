from pathlib import Path

from pipelines.runner import Pipeline, Node, FnStage
from pipelines.renpy.fns import (
    generate_premise,
    generate_story,
    graph_from_story,
    beat_map_from_story,
    write_node_scripts,
    generate_asset_manifest,
    generate_images,
    build,
)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

RENPY_PIPELINE = Pipeline(
    name="renpy",
    prompts_dir=_PROMPTS_DIR,
    nodes=[

        Node("premise", [
            FnStage(
                id="premise",
                fn=generate_premise,
                inputs=["brief.json"],
                output="premise.json",
                prompt_file="premise.txt",
                max_tokens=50000,
            ),
        ]),

        Node("story", [
            FnStage(
                id="story",
                fn=generate_story,
                inputs=["brief.json", "premise.json"],
                output="story.json",
                prompt_file="story_scenes.txt",
                max_tokens=50000,
            ),
        ]),

        Node("graph", [
            FnStage(
                id="graph",
                fn=graph_from_story,
                inputs=["story.json"],
                output="graph.json",
            ),
        ]),

        Node("beat_map", [
            FnStage(
                id="beat_map",
                fn=beat_map_from_story,
                inputs=["story.json"],
                output="beat_map.json",
            ),
        ]),

        Node("node_scripts", [
            FnStage(
                id="node_scripts",
                fn=write_node_scripts,
                inputs=["premise.json", "graph.json", "beat_map.json"],
                output="node_scripts.json",
                prompt_file="character_line.txt",
                max_tokens=50000,
            ),
        ]),

        Node("asset_manifest", [
            FnStage(
                id="asset_manifest",
                fn=generate_asset_manifest,
                inputs=["premise.json", "beat_map.json"],
                output="asset_manifest.json",
                prompt_file="asset_manifest.txt",
                max_tokens=50000,
            ),
        ]),

        Node("images", [
            FnStage(
                id="images",
                fn=generate_images,
                inputs=["premise.json", "asset_manifest.json"],
                output="images_result.json",
            ),
        ]),

        Node("build", [
            FnStage(
                id="build",
                fn=build,
                inputs=["brief.json", "premise.json", "asset_manifest.json", "node_scripts.json"],
                output="build_result.json",
            ),
        ]),

    ],
)
