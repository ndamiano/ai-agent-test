"""Scene refinement — the agent that fixes what placement got wrong.

The agent works through object refinement, then terrain refinement, in that
order: reseating an object onto terrain you are about to flatten is wasted
work, and flattening ground under an object whose scale is wrong deforms the
wrong footprint. Objects with wrong scale or implausible pose are corrected by
editing their placement transform; objects whose reconstruction is too poor are
regenerated from the coarse mesh and the object-centric image; and then
object-terrain contact is fixed by co-deforming the two, moving the object or
locally displacing the ground under its footprint, with every edit confined to
the support region so the landform survives.

The agent is given measurements as well as pictures. Penetration and perfect
seating look identical from most viewpoints, and an agent working from renders
alone would be guessing at exactly the defects this stage exists to remove.
`diagnose` measures, the agent decides.

Terrain edits write the height field back, so a refined scene is a different
world and not merely a different set of transforms.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..llm import LLMHarness, Message, image_part, tool
from ..terrain.models import TerrainPlan
from .camera import Camera
from .diagnose import footprint, summarise, survey

_HERE = Path(__file__).parent
REFINE_PROMPT = (_HERE / "scene_refine_prompt.txt").read_text().strip()

EMBED = 0.02      # of an object's height, so it rests in the ground not on it
MAX_NUDGE_M = 3.0  # how far one call may move an object


class SceneEditor:
    """Applies the agent's corrections to the objects and the ground under them.

    Holds a working copy of the height field. Terrain edits are real edits: they
    change the world, and `write` puts them back on disk.
    """

    def __init__(
        self,
        objects: list[dict],
        height: np.ndarray,
        plan: TerrainPlan,
    ) -> None:
        self.objects = [dict(item) for item in objects]
        self.height = height.astype(np.float32).copy()
        self.plan = plan
        self.size_m = plan.world.size_m
        self.changes: list[str] = []
        self.regenerate: list[int] = []
        self.dropped: list[int] = []
        self.done = False
        self.summary = ""

    # -- state ---------------------------------------------------------------

    def _find(self, index: int) -> dict | None:
        for item in self.objects:
            if item["index"] == index:
                return item
        return None

    def reports(self) -> list[dict]:
        return survey(self.objects, self.height, self.size_m)

    def report_text(self) -> str:
        return summarise(self.reports())

    def _restate(self, item: dict) -> str:
        """One object's defects after an edit, so the agent sees the effect."""
        from .diagnose import inspect

        fresh = inspect(item, self.height, self.size_m)
        if not fresh["defects"]:
            return "it now passes every check"
        return "remaining: " + "; ".join(fresh["defects"])

    # -- tools ---------------------------------------------------------------

    @property
    def tools(self) -> list:
        @tool
        def resize_object(index: int, size_m: float) -> str:
            """Set an object's real size, as its longest horizontal dimension in metres.

            Its height scales with it — the mesh keeps its proportions. Use this
            when the measured size disagrees with what the thing plainly is.

            Args:
                index: The object's index, from the report.
                size_m: Longest horizontal dimension in metres.
            """
            item = self._find(index)
            if item is None:
                return f"error: no object {index}"
            if not 0.05 <= size_m <= 200.0:
                return f"error: {size_m} m is not a plausible size"
            ratio = size_m / max(item["size_m"], 1e-6)
            item["size_m"] = float(size_m)
            item["height_m"] = float(item["height_m"] * ratio)
            self.changes.append(
                f"resized [{index}] {item['category']} to {size_m:g} m"
            )
            return f"resized to {size_m:g} m ({item['height_m']:.2f} m tall); {self._restate(item)}"

        @tool
        def turn_object(index: int, yaw_deg: float) -> str:
            """Set an object's heading in degrees about the vertical axis.

            Single-view reconstruction cannot recover a heading, so every object
            starts facing the camera that saw it. Turn anything whose orientation
            reads wrong — a vehicle across a track it should follow, wreckage
            square to a slope it should lie down.

            Args:
                index: The object's index, from the report.
                yaw_deg: Heading in degrees.
            """
            item = self._find(index)
            if item is None:
                return f"error: no object {index}"
            item["yaw_deg"] = float(yaw_deg) % 360.0
            self.changes.append(f"turned [{index}] to {item['yaw_deg']:g} degrees")
            return f"turned to {item['yaw_deg']:g} degrees"

        @tool
        def move_object(index: int, east_m: float, south_m: float) -> str:
            """Shift an object across the ground and reseat it.

            For separating objects that intersect each other, or moving one off
            ground that cannot support it. Small moves only — the composition
            decided where things go, and this is a correction, not a relayout.

            Args:
                index: The object's index, from the report.
                east_m: Metres to move east. Negative is west.
                south_m: Metres to move south. Negative is north.
            """
            item = self._find(index)
            if item is None:
                return f"error: no object {index}"
            if np.hypot(east_m, south_m) > MAX_NUDGE_M:
                return (
                    f"error: {np.hypot(east_m, south_m):.1f} m is further than one "
                    f"move may go ({MAX_NUDGE_M:g} m)"
                )
            item["position"][0] += float(east_m)
            item["position"][2] += float(south_m)
            self._seat(item)
            self.changes.append(f"moved [{index}] by ({east_m:g}, {south_m:g}) m")
            return f"moved and reseated; {self._restate(item)}"

        @tool
        def seat_object(index: int) -> str:
            """Sit an object properly on the ground it stands on.

            Solves its height so nothing hovers and nothing is buried, given the
            terrain under its footprint. Try this before flattening: a slope an
            object can simply be lowered onto does not need the ground changed.

            Args:
                index: The object's index, from the report.
            """
            item = self._find(index)
            if item is None:
                return f"error: no object {index}"
            self._seat(item)
            self.changes.append(f"seated [{index}] {item['category']}")
            return f"seated at y={item['position'][1]:.2f}; {self._restate(item)}"

        @tool
        def flatten_under(index: int, strength: float = 1.0) -> str:
            """Deform the ground under one object so it has something to stand on.

            The tool of last resort for contact: the ground within the object's
            footprint is pulled towards the plane it sits on, falling off
            smoothly to nothing just outside it. Everything beyond the support
            region is untouched, so the landform survives.

            Use it when an object genuinely belongs where it is and the ground
            there cannot hold it. Do not use it to fix an object that is the
            wrong size.

            Args:
                index: The object's index, from the report.
                strength: How completely to flatten, 0 to 1. Below 1 leaves some
                    of the original ground, which reads better under debris than
                    under a vehicle.
            """
            item = self._find(index)
            if item is None:
                return f"error: no object {index}"
            strength = float(np.clip(strength, 0.0, 1.0))
            changed = self._flatten(item, strength)
            self._seat(item)
            self.changes.append(
                f"flattened under [{index}] at strength {strength:g}"
            )
            return (
                f"flattened {changed} cells under it, moved up to "
                f"{self._last_displacement:.2f} m; {self._restate(item)}"
            )

        @tool
        def regenerate_object(index: int, reason: str) -> str:
            """Queue an object for reconstruction again, at higher quality.

            For a mesh that is a flat slab or is otherwise too poor to stand in
            the scene. It keeps its placement — position, size and heading are
            inherited, so this replaces the geometry and nothing else.

            Expensive, and it happens after this loop rather than now. Reserve it
            for geometry that cannot be corrected by any transform.

            Args:
                index: The object's index, from the report.
                reason: What is wrong with the current mesh.
            """
            item = self._find(index)
            if item is None:
                return f"error: no object {index}"
            if index in self.regenerate:
                return f"[{index}] is already queued for regeneration"
            self.regenerate.append(index)
            self.changes.append(f"queued [{index}] for regeneration: {reason}")
            return (
                f"queued; it will be rebuilt after this pass and will keep its "
                f"placement. {len(self.regenerate)} queued so far"
            )

        @tool
        def remove_object(index: int, reason: str) -> str:
            """Take an object out of the scene.

            For something that should not be there at all — a duplicate, a piece
            of ground that was segmented as an object, a reconstruction with
            nothing recoverable in it. Not for anything a transform can fix.

            Args:
                index: The object's index, from the report.
                reason: Why it does not belong.
            """
            item = self._find(index)
            if item is None:
                return f"error: no object {index}"
            self.objects = [o for o in self.objects if o["index"] != index]
            self.dropped.append(index)
            self.changes.append(f"removed [{index}] {item['category']}: {reason}")
            return f"removed; {len(self.objects)} objects left"

        @tool
        def finish_refinement(summary: str) -> str:
            """Call when every object passes its checks or cannot be improved further.

            Args:
                summary: What you changed and what is still wrong, in a sentence
                    or two.
            """
            self.done = True
            self.summary = summary
            return "refinement complete"

        return [
            resize_object,
            turn_object,
            move_object,
            seat_object,
            flatten_under,
            regenerate_object,
            remove_object,
            finish_refinement,
        ]

    # -- the edits themselves ------------------------------------------------

    def _seat(self, item: dict) -> None:
        """Put an object's base where the ground under its footprint is highest.

        The highest corner rather than the average: an object seated on the mean
        of uneven ground has its high corner through the terrain, and ground
        through an object is a worse defect than air under it. What remains is
        what `flatten_under` is for.
        """
        clearances = footprint(self.height, self.size_m, item["position"], item["size_m"])
        highest_ground = item["position"][1] - min(clearances)
        item["position"][1] = float(
            highest_ground - EMBED * max(item["height_m"], 0.05)
        )

    def _flatten(self, item: dict, strength: float) -> int:
        """Pull the ground under one object towards the plane it rests on."""
        resolution = self.height.shape[0]
        per_metre = resolution / self.size_m
        # The footprint is sampled at the corners of a square of side size_m, so
        # its corners lie 0.71 * size_m from the centre. A disc smaller than that
        # flattens only ground the contact checks never look at: the object still
        # floats by exactly as much, and the support score gets worse because the
        # centre sample moved and the corners did not.
        inner_m = max(item["size_m"] * 0.75, 0.25)
        outer_m = max(item["size_m"] * 1.1, 0.4)
        cx, cz = item["position"][0] * per_metre, item["position"][2] * per_metre
        outer = outer_m * per_metre
        inner = inner_m * per_metre

        x0, x1 = int(max(0, cx - outer - 1)), int(min(resolution, cx + outer + 2))
        z0, z1 = int(max(0, cz - outer - 1)), int(min(resolution, cz + outer + 2))
        if x1 <= x0 or z1 <= z0:
            self._last_displacement = 0.0
            return 0

        zs, xs = np.mgrid[z0:z1, x0:x1]
        distance = np.hypot(xs - cx, zs - cz)
        # flat under the object, then smoothstep out to nothing, so the patch
        # meets the surrounding ground without a step
        weight = np.clip((outer - distance) / max(outer - inner, 1e-6), 0.0, 1.0)
        weight = weight * weight * (3.0 - 2.0 * weight) * strength

        target = float(item["position"][1] + EMBED * max(item["height_m"], 0.05))
        patch = self.height[z0:z1, x0:x1]
        updated = patch * (1.0 - weight) + target * weight
        self._last_displacement = float(np.abs(updated - patch).max())
        self.height[z0:z1, x0:x1] = updated
        return int((weight > 0.01).sum())

    _last_displacement: float = 0.0

    # -- persistence ---------------------------------------------------------

    def write(self, out_dir: Path | str) -> None:
        """Write the refined objects and, if it changed, the refined height field."""
        out_dir = Path(out_dir)
        (out_dir / "objects.json").write_text(json.dumps(self.objects, indent=1))
        np.save(out_dir / "heightmap.npy", self.height)


def refine_scene(
    out_dir: Path | str,
    plan: TerrainPlan,
    *,
    rounds: int = 4,
    region_cameras: list[Camera] | None = None,
) -> SceneEditor:
    """Run the refinement loop over every placed object in a world.

    The engine renders beside the llm call, which is what makes a round cost
    seconds rather than a model swap.
    """
    from ..terrain.render import render

    out_dir = Path(out_dir)
    objects = json.loads((out_dir / "objects.json").read_text())
    height = np.load(out_dir / "heightmap.npy").astype(np.float32)
    editor = SceneEditor(objects, height, plan)

    if not objects:
        editor.summary = "nothing placed to refine"
        return editor

    cameras = region_cameras or _cameras_for(out_dir, objects)

    harness = LLMHarness(tools=editor.tools, system=REFINE_PROMPT, temperature=0.4)
    for round_number in range(1, rounds + 1):
        editor.write(out_dir)
        views = render(
            plan, out_dir,
            cameras=[c.shot(f"refine_{round_number}_{i}.png")
                     for i, c in enumerate(cameras)],
            resolution=f"{cameras[0].width}x{cameras[0].height}",
        )
        before = len(editor.changes)
        message = Message("user", [
            {"type": "text", "text": (
                f"Round {round_number} of {rounds}.\n\n"
                f"{editor.report_text()}\n\n"
                f"Views of the region with the objects in place follow. "
                f"Fix what the report and the renders agree is wrong."
            )},
            *[image_part(view) for view in views],
        ])
        harness.send_message_with_tools([message])

        made = editor.changes[before:]
        for change in made:
            print(f"[scene] round {round_number}: {change}")
        if editor.done:
            print(f"[scene] finished: {editor.summary}")
            break
        if not made:
            print(f"[scene] round {round_number} made no changes — stopping")
            break

    editor.write(out_dir)
    return editor


def _cameras_for(out_dir: Path, objects: list[dict]) -> list[Camera]:
    """The region cameras the objects were placed against, one per region."""
    cameras = []
    for region in dict.fromkeys(item["region_id"] for item in objects):
        path = out_dir / f"camera_{region}.json"
        if path.exists():
            cameras.append(Camera.from_dict(json.loads(path.read_text())))
    return cameras


def apply_regenerations(editor: SceneEditor, out_dir: Path | str) -> int:
    """Rebuild the meshes the agent queued, keeping their placements.

    TRELLIS2 takes only an image, with no coarse mesh to constrain the rebuild,
    so this is a re-roll at a new seed rather than a refinement: it fixes a slab
    that came from an unlucky sample and does nothing at all for one that came
    from a crop too small or too dark to carry geometry.
    """
    from ..backends import MeshModel

    if not editor.regenerate:
        return 0
    out_dir = Path(out_dir)
    rows = [item for item in editor.objects if item["index"] in editor.regenerate]
    produced = MeshModel().reconstruct(
        [row["mesh_source"] for row in rows], out_dir / "objects" / "regenerated",
        seed=7919, overwrite=True,
    )
    remade = 0
    for item in rows:
        glb = produced.get(item["mesh_source"])
        if glb is not None:
            item["mesh"] = str(glb)
            remade += 1
    editor.write(out_dir)
    print(f"[scene] regenerated {remade} of {len(rows)} meshes")
    return remade


__all__ = ["SceneEditor", "refine_scene", "apply_regenerations", "REFINE_PROMPT"]
