from pathlib import Path

from pipelines.runner import Pipeline, Node, FnStage
from pipelines.renpy.fns import (
    generate_graph,
    generate_premise,
    generate_endings,
    backward_fill,
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

        Node("graph", [
            FnStage(
                id="graph",
                fn=generate_graph,
                inputs=["brief.json"],
                output="graph.json",
            ),
        ]),

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

        Node("endings", [
            FnStage(
                id="endings",
                fn=generate_endings,
                inputs=["premise.json", "graph.json"],
                output="endings.json",
                prompt_file="endings.txt",
                max_tokens=50000,
            ),
        ]),

        Node("beat_map", [
            FnStage(
                id="beat_map",
                fn=backward_fill,
                inputs=["premise.json", "graph.json", "endings.json"],
                output="beat_map.json",
                prompt_file="backward_fill.txt",
                max_tokens=50000,
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
