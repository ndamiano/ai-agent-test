"""Finding the objects in a composition, by asking a vision model to record them.

    found = locate(composition)          # [(label, bbox, size_m, prompt), ...]

This replaces text-prompted segmentation. The paper's §2.3.2 hands the regional
plan's categories to a segmenter and asks, of each in turn, where it is. That
gives the plan authority it has not earned: the composition is the thing being
reconstructed, and the plan is a brief the image model was free to ignore.

It did ignore it. One region of the cliff city was briefed for fifteen objects
and painted four, and across three regions a third of what was actually in the
pictures -- people, a dome, a statue, benches -- had no category and so was
never looked for. Meanwhile eight jars that were ordered and never painted were
dutifully "found": SAM3, asked eight times for a water jar, returned eight
courtyards.

The model records objects one at a time through `add_object`, which is a tool
rather than a line of JSON for three reasons. Each box is validated as it
arrives, so a bad coordinate comes back as an error the model can correct rather
than corrupting a reply. A box that lands on one already recorded is refused, so
the model is told about the collision instead of the pipeline finding it later.
And each object carries its own prompt, written while looking at that object,
which no batched pass produces -- asked to describe a list, the model writes one
description per KIND and every stall in the row gets the same sentence.

Asked for a single JSON array instead, the same model on the same picture
collapsed on the densest scene: after about twenty-five real objects it locked
into emitting identical barrels stepping right by fifteen units, ran off the
edge of the image, and generated until it hit the token limit. Two hundred junk
entries after twenty-five good ones, and no way to tell from the numbers where
the good ones stopped. Through a tool that cannot happen: the second barrel in
the march overlaps the first and is refused.

Three details are load-bearing, and all three are measured.

The user message names the image's pixel dimensions. Without it the model
reports the dozen obvious things and stops; with it, it finds the small ones.
Over three trials each: twenty to twenty-two objects with, twelve to thirteen
without, and the whole difference is objects under thirty pixels.

The vocabulary is generic. Naming the kinds a particular world happens to hold
anchors the model to them and finds fewer things, not more -- thirty against
twenty for the same prompt with a generic noun list.

The first `finish_objects` is refused. Asked whether it is sure and told to look
again, the model finds more, and on a jungle village it found a great deal more:
ten objects before the nudge and thirty-three after.

What does NOT help, also measured: telling it how to look. Asking for a
systematic near-to-far sweep, or for the smallest objects first, both drove
recall of objects under thirty pixels to exactly zero.

Which generalises, and is the reason the Field descriptions below are as short
as they are. They are prompt, not documentation, and they compete with the
picture: rewriting those four one-liners into fuller ones -- same system prompt,
same tools, same everything else -- took this from thirty-five objects to eleven,
reproducibly. One of them offered "market stall", not "row of market stalls" as
an example of what not to write, and the model went and recorded a row of
stalls. State rules once, positively, in the system prompt, and leave the schema
alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pydantic import Field, ValidationError, model_validator

from ..llm import LLMHarness, Message, tool
from .models import Strict
from .subject import DEFAULT_MODEL, _image_part

# Boxes come back on this grid whatever the image's real size, so every one has
# to be rescaled before it means anything in pixels.
GRID = 1000

# Two boxes overlapping this much are the same object recorded twice.
DUPE_IOU = 0.55

# How many times to go round asking for more. The loop stops earlier when the
# model finishes or a round adds nothing; this only bounds the pathological case.
MAX_ROUNDS = 6

FIND_SYSTEM = """
You look at a picture and record every object in it, one at a time.

An object is anything DISCRETE a person could walk into, pick up, or stand next
to: buildings, towers, walls, stalls, steps, containers, seats, statues, people,
vehicles, machines, equipment, trees, bushes.

Call add_object once for each one. Box only the object itself -- not its shadow,
not smoke coming off it, not the ground it stands on -- and make the box include
the point where it meets the ground.

Never group similar things together. Every single one gets its own box and its
own call, however many there are and however alike they look. There is no such
object as a row, a group, a pile, a crowd or a set of anything: there are only
the individual things that make it up.

Do not record ground or sky: rock faces, bare soil, paving, open water.

Work through the picture until there is nothing left to record, including the
small objects, then call finish_objects.
""".strip()

OPENING = (
    "This image is {width} by {height} pixels. Record every object you can see."
)

AGAIN = (
    "Keep going. Record any object you have not recorded yet, then call "
    "finish_objects when there is nothing left."
)

ARE_YOU_SURE = (
    "{count} objects recorded. Are you sure you are finished? Look over the "
    "picture once more -- small objects, things standing in a row, things partly "
    "hidden behind others -- record whatever is missing, then call finish_objects "
    "again when you are certain."
)


class GroundingError(RuntimeError):
    """The vision model never recorded anything for this image."""


class Box(Strict):
    # No class docstring, and no min_length on the string fields. Both are sent
    # to the model as part of the tool schema -- the docstring as a description
    # of the parameter, the lengths as constraints -- and the schema is prompt.
    # See the note above the field descriptions.

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

    @property
    def area(self) -> int:
        x0, y0, x1, y1 = self.bbox
        return max(x1 - x0, 0) * max(y1 - y0, 0)


def _coerce(box: object) -> Box | str:
    """`box` as a Box, or the reason it is not, worded for the model."""
    if isinstance(box, Box):
        return box
    try:
        if isinstance(box, str):
            return Box.model_validate_json(box)
        if isinstance(box, dict):
            return Box.model_validate(box)
    except ValidationError as exc:
        return f"error: {exc.error_count()} problem(s) with that object: {exc}"
    return f"error: expected an object with label, bbox_2d, size_m and prompt, got {type(box).__name__}"


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
            # the argument does not always arrive as the model it is annotated
            # with: on some images this model hands over the whole call as a JSON
            # string instead, deterministically, and an unhandled AttributeError
            # then loses every object found so far
            box = _coerce(box)
            if isinstance(box, str):
                return box  # the failure, phrased for the model to correct
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
                # refused once on purpose: asked whether it is sure, the model
                # goes back and finds more, and on the jungle fringe it went from
                # ten objects to thirty-three
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
    model: str = DEFAULT_MODEL,
    max_rounds: int = MAX_ROUNDS,
    verbose: bool = True,
) -> list[Found]:
    """Every object in the composition, boxed, sized and described.

    The model records a batch and stops rather than working to the end, so it is
    asked again until it either finishes or a round turns up nothing new.
    """
    composition = Path(composition)
    width, height = Image.open(composition).size
    recorder = ObjectRecorder()
    harness = LLMHarness(
        model=model, tools=recorder.tools, system=FIND_SYSTEM, temperature=0.0,
    )

    text = OPENING.format(width=width, height=height)
    for round_number in range(max_rounds):
        before = len(recorder.boxes)
        harness.send_message_with_tools([Message("user", [
            {"type": "text", "text": text},
            _image_part(composition),
        ])])
        gained = len(recorder.boxes) - before
        if verbose:
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
    if verbose:
        print(f"[ground] {composition.name}: {len(found)} objects")
    return found


def draw_boxes(composition: Path | str, found: list[Found], out: Path | str) -> Path:
    """Write a copy of the composition with the boxes on it. For looking at."""
    from PIL import ImageDraw

    image = Image.open(composition).convert("RGB")
    draw = ImageDraw.Draw(image)
    for item in found:
        draw.rectangle(list(item.bbox), outline=(255, 60, 60), width=3)
        draw.text((item.bbox[0] + 3, item.bbox[1] + 2), item.label[:18],
                  fill=(255, 255, 0))
    out = Path(out)
    image.save(out)
    return out


__all__ = [
    "Box", "Found", "GroundingError", "ObjectRecorder", "locate", "draw_boxes",
    "FIND_SYSTEM", "GRID", "DUPE_IOU",
]
