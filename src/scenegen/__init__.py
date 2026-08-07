"""Scene composition: baked ground + kit-assembled structures + zone-scattered props + a light
plan, rendered from a spec over a logic grid. Ported from the 2026-08-06 24-round exploration
(record: the session's notebook artifact); the laws it encodes live in the project memory's
tile-ground doctrine.

Currently STANDALONE and unwired, like worldgen: how a build reaches it — vendored game-side
library, platform render stage, or both — is an open decision, not a dependency. Nothing here
enqueues or talks to a GPU; part sprites and material exemplars arrive as images (parts.py builds
the payloads a caller can put on the image queue).

The division the rounds settled: code owns everything spatial and semantic — layouts, footprints,
door cells, adjacency, scatter zones, light — and diffusion paints materials and parts. Structures
are assembled from reused parts so every door is identical and sits on a cell game logic knows;
restyling happens at the part level, never by img2img over an assembled building (measured: no
denoise both keeps structure and changes style).
"""

from scenegen.compose import compose_scene
from scenegen.kit import BuildingKit, assemble
from scenegen.layouts import glade_layout, town_layout

__all__ = ["compose_scene", "BuildingKit", "assemble", "town_layout", "glade_layout"]
