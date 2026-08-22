"""Terrain refinement (§2.2.3).

"The terrain refinement agent re-renders the scene from predefined viewpoints,
inspects its geometry, materials, scattering results, and rendering
configuration, and performs localized corrections according to the detected
issues... The loop continues until no substantial issue is detected or a
predefined iteration budget is reached."

The agent edits the specification and the world is rebuilt from it. Nothing here
touches geometry, which is the property that makes the loop safe: every state
the world passes through is a state the specification can produce again, so a
round that makes things worse is undone by changing a number back.

What makes this cheap enough to iterate is measured rather than assumed. A
rebuild is 0.7 s of numpy and a five-view render is a few seconds of headless
browser; only the llm call itself pays for the queue's round trip. A round is
about ten seconds.

Regenerating a material is a slower call on the image queue, and the reason
`regenerate_material` queues its work for after the loop rather than doing it
inline.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from ..backends import ImageModel
from ..llm import LLMHarness, Message, tool
from .construct import construct
from .models import GeomorphOp, NoiseBand, TerrainPlan
from .render import render

_HERE = Path(__file__).parent
REFINE_PROMPT = (_HERE / "refine_prompt.txt").read_text().strip()

DEFAULT_MODEL = "qwen3.8_27b"


def _image_part(path: Path) -> dict:
    """One render, as the content part an OpenAI-compatible endpoint expects."""
    data = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


class PlanEditor:
    """Applies the agent's corrections to a working copy of P_terrain.

    Every edit revalidates the whole plan. A correction that breaks a constraint
    — a noise wavelength longer than the world, a coverage that no longer adds
    up — is rejected and reported back as a tool result, with the plan left as
    it was, so the agent can try a different number rather than producing a
    specification nothing can build.
    """

    def __init__(self, plan: TerrainPlan) -> None:
        self.plan = plan
        self.changes: list[str] = []
        self.queued_materials: list[dict] = []
        self.done = False
        self.summary = ""

    def _apply(self, data: dict, description: str) -> str:
        try:
            self.plan = TerrainPlan.model_validate(data)
        except Exception as e:
            first = str(e).splitlines()
            detail = next((line.strip() for line in first if "Value error" in line), str(e))
            return f"rejected ({description}): {detail[:220]}"
        self.changes.append(description)
        return f"applied: {description}"

    @property
    def tools(self) -> list:
        @tool
        def set_region_elevation(region_id: str, base_elevation_m: float) -> str:
            """Set the height a region sits at, in metres.

            This is what makes a world read as layered. The difference between
            two regions' base elevations is the step between them.

            Args:
                region_id: Which region.
                base_elevation_m: Its new base height in metres.
            """
            data = self.plan.model_dump()
            for row in data["terrain"]:
                if row["region_id"] == region_id:
                    was = row["base_elevation_m"]
                    row["base_elevation_m"] = base_elevation_m
                    return self._apply(
                        data, f"{region_id} base elevation {was:g} -> {base_elevation_m:g} m"
                    )
            return f"error: no region {region_id!r}"

        @tool
        def set_region_noise(region_id: str, noise: list[NoiseBand]) -> str:
            """Replace a region's noise bands.

            Two or three bands: a long wavelength for the shape of the land, a
            short one for surface roughness. Amplitude is peak-to-trough metres.

            Args:
                region_id: Which region.
                noise: The complete new list of bands for it.
            """
            data = self.plan.model_dump()
            for row in data["terrain"]:
                if row["region_id"] == region_id:
                    row["noise"] = [b.model_dump() for b in noise]
                    total = sum(b.amplitude_m for b in noise)
                    return self._apply(
                        data, f"{region_id} noise -> {len(noise)} bands, {total:g} m total"
                    )
            return f"error: no region {region_id!r}"

        @tool
        def set_region_operators(region_id: str, operators: list[GeomorphOp]) -> str:
            """Replace a region's landform operators.

            Use this to add relief a region is missing, to remove a landform that
            does not belong, or to change how much height one contributes.

            Args:
                region_id: Which region.
                operators: The complete new list of operators for it.
            """
            data = self.plan.model_dump()
            for row in data["terrain"]:
                if row["region_id"] == region_id:
                    row["operators"] = [o.model_dump() for o in operators]
                    kinds = [o.kind for o in operators] or ["<none>"]
                    return self._apply(data, f"{region_id} operators -> {kinds}")
            return f"error: no region {region_id!r}"

        @tool
        def set_material_scale(region_id: str, scale_m: float) -> str:
            """Set how many metres one repeat of a region's surface covers.

            Raise it when the ground shows a visible repeating pattern; lower it
            when the surface looks stretched or too coarse for what it is.

            Args:
                region_id: Which region.
                scale_m: Metres per repeat.
            """
            data = self.plan.model_dump()
            for row in data["materials"]:
                if row["region_id"] == region_id:
                    was = row["scale_m"]
                    row["scale_m"] = scale_m
                    return self._apply(
                        data, f"{region_id} material scale {was:g} -> {scale_m:g} m"
                    )
            return f"error: no region {region_id!r}"

        @tool
        def set_asset_density(category: str, per_hectare: float) -> str:
            """Set how many instances of a scattered category there are per hectare.

            Args:
                category: The asset category.
                per_hectare: Instances per 100 m x 100 m.
            """
            data = self.plan.model_dump()
            for row in data["assets"]:
                if row["category"] == category:
                    was = row["per_hectare"]
                    row["per_hectare"] = per_hectare
                    return self._apply(
                        data, f"{category!r} density {was:g} -> {per_hectare:g}/ha"
                    )
            return f"error: no asset {category!r}"

        @tool
        def set_asset_size(category: str, height_m: float) -> str:
            """Set the typical height of one instance of a category, in metres.

            Meshes are normalised, so this is the only thing that decides how big
            the thing appears.

            Args:
                category: The asset category.
                height_m: Typical height in metres.
            """
            data = self.plan.model_dump()
            for row in data["assets"]:
                if row["category"] == category:
                    was = row["height_m"]
                    row["height_m"] = height_m
                    return self._apply(data, f"{category!r} height {was:g} -> {height_m:g} m")
            return f"error: no asset {category!r}"

        @tool
        def set_boundary_blend(boundary_blend_m: float) -> str:
            """Set how wide the transition between regions is, in metres.

            Narrow reads as an escarpment or a shoreline; wide reads as a
            gradual change. Too wide and every region blends into one average.

            Args:
                boundary_blend_m: Blend width in metres.
            """
            data = self.plan.model_dump()
            was = data["world"]["boundary_blend_m"]
            data["world"]["boundary_blend_m"] = boundary_blend_m
            return self._apply(data, f"boundary blend {was:g} -> {boundary_blend_m:g} m")

        @tool
        def regenerate_material(region_id: str, surface: str, appearance: str) -> str:
            """Ask for a region's surface texture to be generated again. Expensive.

            Only when the surface is the wrong thing entirely — sand where there
            should be broken rock. If it is the right thing at the wrong size,
            use set_material_scale instead, which costs nothing.

            The new texture is made after this loop ends, not now.

            Args:
                region_id: Which region.
                surface: What the ground should be.
                appearance: Colour, roughness and fine structure, in a sentence
                    or two. Describe a uniform surface with no large features.
            """
            data = self.plan.model_dump()
            for row in data["materials"]:
                if row["region_id"] == region_id:
                    row["surface"] = surface
                    row["appearance"] = appearance
                    result = self._apply(data, f"{region_id} material -> {surface!r}")
                    if result.startswith("applied"):
                        self.queued_materials.append({"region_id": region_id})
                        return f"{result}; queued for regeneration after this loop"
                    return result
            return f"error: no region {region_id!r}"

        @tool
        def finish(summary: str) -> str:
            """Call when the renders show nothing further worth correcting.

            Args:
                summary: One or two sentences on the state of the terrain.
            """
            self.done = True
            self.summary = summary.strip()
            return "refinement finished"

        return [
            set_region_elevation,
            set_region_noise,
            set_region_operators,
            set_material_scale,
            set_asset_density,
            set_asset_size,
            set_boundary_blend,
            regenerate_material,
            finish,
        ]


def _briefing(plan: TerrainPlan, summary: dict, round_number: int, rounds: int) -> str:
    """What the agent is told in words, alongside the renders."""
    regions = "\n".join(
        f"  {row.region_id:14s} {row.category:14s} "
        f"base {plan.terrain_for(row.region_id).base_elevation_m:7.1f} m  "
        f"material every {plan.material_for(row.region_id).scale_m:g} m  "
        f"operators {[o.kind for o in plan.terrain_for(row.region_id).operators] or '-'}"
        for row in plan.layout
    )
    assets = "\n".join(
        f"  {a.category:26s} {a.per_hectare:6g}/ha  {a.height_m:4g} m tall  "
        f"placed {summary['scatter'].get(a.category, 0)}"
        for a in plan.assets
    ) or "  (none)"
    elevation = summary["elevation_m"]
    slope = summary["slope_deg"]
    return (
        f"Refinement round {round_number} of {rounds}.\n\n"
        f"World: {plan.world.size_m:g} m square, boundary blend "
        f"{plan.world.boundary_blend_m:g} m.\n"
        f"Elevation: {elevation['min']:g} to {elevation['max']:g} m "
        f"(mean {elevation['mean']:g}). Slope: median {slope['median']:g} deg, "
        f"95th {slope['p95']:g} deg.\n\n"
        f"Regions:\n{regions}\n\nScattered:\n{assets}\n\n"
        f"The images are a top-down view followed by views from four sides. "
        f"Correct what you can see, then call finish."
    )


def refine(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    rounds: int = 3,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
) -> TerrainPlan:
    """Look, correct, rebuild, repeat. Returns the refined plan, and writes it.

    Runs cool: this stage is reading renders and adjusting numbers against what
    it sees, and invention is exactly what it must not do.
    """
    out_dir = Path(out_dir)
    editor = PlanEditor(plan)

    harness = LLMHarness(model=model, system=REFINE_PROMPT, temperature=temperature)
    for round_number in range(1, rounds + 1):
        summary = construct(editor.plan, out_dir)
        views = render(editor.plan, out_dir)
        before = len(editor.changes)

        message = Message("user", [
            {"type": "text", "text": _briefing(
                editor.plan, summary, round_number, rounds
            )},
            *[_image_part(view) for view in views],
        ])
        harness.send_message_with_tools([message], tools=editor.tools)

        made = editor.changes[before:]
        for change in made:
            print(f"[refine] round {round_number}: {change}")
        if editor.done:
            print(f"[refine] finished: {editor.summary}")
            break
        if not made:
            # nothing changed and nothing said to stop: another identical
            # round would see an identical render and decide the same thing
            print(f"[refine] round {round_number} made no changes — stopping")
            break

    if editor.queued_materials:
        from . import materials as materials_module

        print(f"[refine] regenerating {len(editor.queued_materials)} materials")
        images = ImageModel()
        remade = materials_module.albedo(
            editor.plan, out_dir / "materials", images=images, overwrite=True
        )
        for path in remade.values():
            materials_module.derive_channels(path)

    construct(editor.plan, out_dir)
    render(editor.plan, out_dir)
    (out_dir / "terrain_plan.json").write_text(editor.plan.model_dump_json(indent=2))
    (out_dir / "refinement.json").write_text(json.dumps({
        "changes": editor.changes,
        "summary": editor.summary,
        "regenerated_materials": [m["region_id"] for m in editor.queued_materials],
    }, indent=2))
    return editor.plan


__all__ = ["refine", "PlanEditor", "REFINE_PROMPT"]
