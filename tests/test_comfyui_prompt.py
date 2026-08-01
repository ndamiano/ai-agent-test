"""The image job a kind resolves to.

A game asks for three different KINDS of picture and they want opposite things: a sprite is cut out
and drawn on top of the game, a tile and a scene ARE the background. Rendering all three through the
one item-icon workflow is what matted a floor down to a handful of planks.

The MATTE is the whole of what a kind changes. It is not the negative: flux schnell runs at cfg 1.0
and ComfyUI skips the uncond pass there, so node 7 never reaches the model whatever it says.
"""

import pytest

from tools.comfyui_tools import build_image_job, build_image_payload, build_img2img_job


def test_the_prompt_is_the_positive_verbatim():
    # The saved prompt is the positive WHOLE, never embedded mid-phrase — wrapping multi-sentence
    # prose inside "a single {X}, one object only, ..." garbled the grammar and pushed the framing
    # keywords past the CLIP window (subject drift).
    job = build_image_job("A rusty iron key with a worn bow. A single object icon, centered.")
    assert job["prompt"] == "A rusty iron key with a worn bow. A single object icon, centered."
    assert job["workflow_override"]["5"]["inputs"]["width"] == \
        job["workflow_override"]["5"]["inputs"]["height"]


def test_a_sprite_is_matted():
    wf = build_image_job("A rusty iron key.", "sprite")["workflow_override"]
    assert wf["9"]["inputs"]["images"] == ["47", 0]      # the output reads BiRefNet


@pytest.mark.parametrize("kind", ["tile", "scene"])
def test_a_background_keeps_its_whole_frame(kind):
    """Matting a floor leaves the ragged fragments of a floor that used to be a floor."""
    wf = build_image_job("Worn wooden floorboards.", kind)["workflow_override"]
    assert "47" not in wf
    assert wf["9"]["inputs"]["images"] == ["8", 0]


def test_the_negative_does_not_vary_by_kind():
    """It cannot mean anything: this sampler runs at cfg 1.0, where the uncond pass is skipped. A
    negative that differed per kind would read as the thing separating them, and it is not."""
    negatives = {build_image_job("A key.", k)["workflow_override"]["7"]["inputs"]["text"]
                 for k in ("sprite", "tile", "scene")}
    assert len(negatives) == 1
    assert build_image_job("A key.")["workflow_override"]["3"]["inputs"]["cfg"] == 1.0


def test_img2img_job_seeds_from_the_init_image():
    job = build_img2img_job("A rusty iron key, now golden.", "init_abc.png", denoise=0.5)
    wf = job["workflow_override"]
    assert wf["50"]["inputs"]["image"] == "init_abc.png"
    assert wf["3"]["inputs"]["denoise"] == 0.5
    # the sampler denoises the ENCODED init image, not an empty latent
    assert wf["3"]["inputs"]["latent_image"] == ["51", 0]
    assert wf["51"]["inputs"]["pixels"] == ["50", 0]
    assert wf["6"]["inputs"]["text"] == "A rusty iron key, now golden."


def test_img2img_honours_the_kind_too():
    """A regenerate of a tile must not put the matte back that the first render left out."""
    wf = build_img2img_job("Worn floorboards.", "init_abc.png", "tile")["workflow_override"]
    assert "47" not in wf
    assert wf["9"]["inputs"]["images"] == ["8", 0]


def test_payload_with_init_image_carries_the_upload():
    payload = build_image_payload("A rusty iron key.", init_image_b64="aGk=")
    assert payload["kind"] == "comfy_image"
    (up,) = payload["uploads"]
    assert up["b64"] == "aGk="
    # the workflow's LoadImage reads exactly the name the worker will upload under
    assert payload["workflow"]["50"]["inputs"]["image"] == up["name"]


def test_payload_without_init_image_has_no_uploads():
    payload = build_image_payload("A rusty iron key.")
    assert "uploads" not in payload
    assert "50" not in payload["workflow"]   # txt2img: empty latent, no LoadImage
