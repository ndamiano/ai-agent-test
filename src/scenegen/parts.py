"""Part prompts and payloads: the measured prompt shapes for kit parts and prop sprites, and the
BiRefNet matte graph a caller runs on the results. Data and payload builders only — nothing here
enqueues; the image queue and its metering belong to the caller.

Measured prompt rules baked into the templates:
- Objects and furniture render through Qwen (characters through NetaYume) — Qwen crushed the
  furniture A/B and NetaYume's furniture is fanart-grade.
- The viewport is described by its VISIBLE FACES ("top surface dominant, front face a thin
  strip"), never by naming a projection — "oblique" alone reads as a side view.
- View class per part role: against-wall parts (doors, windows, hearths, portraits) front view;
  free-standing flat pieces top-dominant; seats and vertical heroes a 3/4 blend.
- Style words that name a CONTAINER ("snes town") contaminate small subjects — a lamp prompt
  carrying "town" renders a tiny town. Style rides adjectives and materials only.
"""

from typing import Dict

FRONT = "seen straight from the front, rectangular, "
TOP_DOMINANT = ("camera high above looking down, the top surface is the large visible face, the "
                "front face only a thin strip along the bottom edge, no side faces, axis-aligned, ")
BLEND_34 = "three-quarter top-down rpg view, "
SUFFIX = "video game sprite part, clean cutout on plain white background"

KIT_PART_VIEWS: Dict[str, str] = {
    "door": FRONT, "window": FRONT, "hearth": FRONT, "portrait": FRONT, "banner": FRONT,
    "sconce": FRONT, "shelf": FRONT,
    "table": TOP_DOMINANT, "bed": TOP_DOMINANT, "rug": TOP_DOMINANT, "counter": TOP_DOMINANT,
    "chair": BLEND_34, "throne": BLEND_34, "statue": BLEND_34, "lantern": BLEND_34,
    "tree": BLEND_34, "fountain": BLEND_34, "well": BLEND_34,
}


def part_prompt(role: str, description: str) -> str:
    """description carries subject + style adjectives/materials; the view rides the role."""
    view = KIT_PART_VIEWS.get(role, BLEND_34)
    return f"{description}, {view}{SUFFIX}"


def matte_graph(image_name: str) -> dict:
    """ComfyUI graph that mattes an uploaded render; the caller uploads under `image_name` and
    reads the SaveImage output."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "BiRefNetRMBG",
              "inputs": {"image": ["1", 0], "model": "BiRefNet-general",
                         "mask_blur": 0, "mask_offset": -1, "invert_output": False,
                         "refine_foreground": False, "background": "Alpha",
                         "background_color": "#ffffff"}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0],
                                                    "filename_prefix": "matte"}},
    }
