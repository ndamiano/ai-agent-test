"""How this pipeline talks to the two generative models it does not host itself.

    ImageModel  ->  Qwen Image (2512) and Qwen Image Edit (2511), through the image queue
    MeshModel   ->  TRELLIS2, through the mesh queue

Both are wrapped as a single object with a couple of methods, because everything
upstream of them should be able to say "draw this" or "reconstruct this" without
knowing which queue answers it.

    from maestro.worldgen.backends import ImageModel, MeshModel

    images = ImageModel()
    concept = images.generate("A high aerial photograph of a desert battlefield.",
                              out="concept.png")
    layout = images.edit("Redraw this map with natural boundaries.", [discs],
                         out="layout.png")

    meshes = MeshModel()
    meshes.reconstruct(["rock.png", "shrub.png"], out_dir="meshes")
"""
from __future__ import annotations

from .images import ImageModel, ImageModelError
from .meshes import MeshModel, MeshModelError

__all__ = ["ImageModel", "ImageModelError", "MeshModel", "MeshModelError"]
