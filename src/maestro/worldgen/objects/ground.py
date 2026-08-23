"""Finding the objects in a composition, by asking a vision model to record them one
at a time through a tool: the plan is a brief the image model was free to ignore, and a
single JSON array collapses into a march of duplicates on dense scenes (measured, see
docs/experiments.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pydantic import Field, model_validator

from ..llm import LLMHarness, Message, Strict, image_part, tool

_HERE = Path(__file__).parent

# Boxes come back on this grid whatever the image's real size, so every one has
# to be rescaled before it means anything in pixels.
GRID = 1000

# Two boxes overlapping this much are the same object recorded twice.
DUPE_IOU = 0.55

# How many times to go round asking for more. The loop stops earlier when the
# model finishes or a round adds nothing; this only bounds the pathological case.
MAX_ROUNDS = 6

FIND_SYSTEM = (_HERE / "find_prompt.txt").read_text().strip()
OPENING = (_HERE / "find_opening.txt").read_text().strip()
AGAIN = (_HERE / "find_again.txt").read_text().strip()
ARE_YOU_SURE = (_HERE / "find_are_you_sure.txt").read_text().strip()


class GroundingError(RuntimeError):
    """The vision model never recorded anything for this image."""


class Box(Strict):
    # No class docstring, and no min_length on the string fields: both reach the
    # model as tool schema, and fuller field text measurably finds fewer objects.

    label: str = Field(
        description="What it is, plainly, two or three words.",
    )
    bbox_2d: list[int] = Field(
        min_length=4, max_length=4,
        description="[x1, y1, x2, y2] on a 0-1000 grid, whatever the image size.",
    )
    size_m: float = Field(
        gt=0, le=200,
        description="Longest side of this one, in metres.",
    )
    prompt: str = Field(
        description="One sentence, under 40 words, that would draw THIS object "
        "alone for 3D reconstruction. The whole object, including parts the "
        "picture hides. Start with the object itself.",
    )

    @model_validator(mode="after")
    def _check_box(self) -> Box:
        x0, y0, x1, y1 = self.bbox_2d
        if not all(0 <= v <= GRID for v in self.bbox_2d):
            raise ValueError(
                f"{self.bbox_2d} is outside the picture; coordinates run 0 to {GRID}"
            )
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"{self.bbox_2d} has no area; x1 must exceed x0 and y1 y0")
        return self


@dataclass
class Found:
    """One recorded object, rescaled into the composition's own pixels."""

    label: str
    bbox: tuple[int, int, int, int]
    size_m: float
    prompt: str


def _iou(a: list[int] | tuple[int, ...], b: list[int] | tuple[int, ...]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    x0, y0 = max(ax0, bx0), max(ay0, by0)
    x1, y1 = min(ax1, bx1), min(ay1, by1)
    overlap = max(0, x1 - x0) * max(0, y1 - y0)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - overlap
    return overlap / union if union > 0 else 0.0


class ObjectRecorder:
    """Accumulates the objects the model records, and refuses the bad ones."""

    def __init__(self) -> None:
        self.boxes: list[Box] = []
        self.done = False
        self.finish_calls = 0

    @property
    def tools(self) -> list:
        """The tool list to hand to `LLMHarness(tools=...)`."""

        @tool
        def add_object(box: Box) -> str:
            """Record one object you can see, with its box, size and a prompt to draw it.

            Only this first line reaches the model -- the schema takes a tool's
            description from it and drops the rest -- so anything the model needs
            to know belongs in FIND_SYSTEM, not here.
            """
            for other in self.boxes:
                if _iou(box.bbox_2d, other.bbox_2d) >= DUPE_IOU:
                    return (
                        f"error: that is where {other.label!r} already is, at "
                        f"{other.bbox_2d}. If this is a different object, box it "
                        f"where it actually stands; if it is the same one, leave it."
                    )
            self.boxes.append(box)
            return f"recorded {box.label!r} ({len(self.boxes)} so far)"

        @tool
        def finish_objects() -> str:
            """Call when every object you can see has been recorded."""
            self.finish_calls += 1
            if self.finish_calls == 1:
                # refused once on purpose: asked whether it is sure, the model finds more
                return ARE_YOU_SURE.format(count=len(self.boxes))
            self.done = True
            return f"finished with {len(self.boxes)} objects"

        return [add_object, finish_objects]

    def scaled(self, width: int, height: int) -> list[Found]:
        """The boxes in composition pixels, dropping any too small to crop."""
        found = []
        for box in self.boxes:
            x0, y0, x1, y1 = box.bbox_2d
            pixels = (
                int(round(x0 / GRID * width)),
                int(round(y0 / GRID * height)),
                int(round(x1 / GRID * width)),
                int(round(y1 / GRID * height)),
            )
            if pixels[2] - pixels[0] < 2 or pixels[3] - pixels[1] < 2:
                continue
            found.append(Found(box.label, pixels, box.size_m, box.prompt))
        return found


def locate(
    composition: Path | str,
    *,
    max_rounds: int = MAX_ROUNDS,
) -> list[Found]:
    """Every object in the composition, boxed, sized and described.

    The model records a batch and stops rather than working to the end, so it is
    asked again until it either finishes or a round turns up nothing new.
    """
    composition = Path(composition)
    width, height = Image.open(composition).size
    recorder = ObjectRecorder()
    harness = LLMHarness(tools=recorder.tools, system=FIND_SYSTEM, temperature=0.0)

    text = OPENING.format(width=width, height=height)
    for round_number in range(max_rounds):
        before = len(recorder.boxes)
        harness.send_message_with_tools([Message("user", [
            {"type": "text", "text": text},
            image_part(composition),
        ])])
        gained = len(recorder.boxes) - before
        print(
            f"[ground] round {round_number + 1}: +{gained} "
            f"({len(recorder.boxes)} objects)"
            + ("  finished" if recorder.done else "")
        )
        if recorder.done:
            break
        # nothing new and it never even tried to finish: it has stopped working
        # rather than run out of things to record, and asking again will not help
        if gained == 0 and recorder.finish_calls == 0:
            break
        text = AGAIN

    found = recorder.scaled(width, height)
    if not found:
        raise GroundingError(f"{composition.name}: no objects recorded")
    print(f"[ground] {composition.name}: {len(found)} objects")
    return found


__all__ = [
    "Box", "Found", "GroundingError", "ObjectRecorder", "locate",
    "FIND_SYSTEM", "GRID", "DUPE_IOU",
]
