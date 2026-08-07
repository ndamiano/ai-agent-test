"""The image job a kind resolves to.

A game asks for three different KINDS of picture and they want opposite things: a sprite is cut out
and drawn on top of the game, a tile and a scene ARE the background. Rendering all three through the
one item-icon workflow is what matted a floor down to a handful of planks.

A kind now picks the MODEL too: sprites and scenes render through NetaYume Lumina (anime checkpoint,
real cfg, so the negative reaches the model), tiles through DreamShaperXL Turbo with a negative that
holds off the photoreal drift and cracks tiles grow without it.
"""

import pytest

from tools.comfyui_tools import build_image_job, build_image_payload, build_img2img_job


def test_the_prompt_is_the_positive_substance():
    # The manifest's saved prompt survives verbatim in job["prompt"], and the positive carries it
    # WHOLE after the quality-tag prefix — embedding multi-sentence prose mid-phrase ("a single
    # {X}, one object only, ...") garbled the grammar and drove subject drift.
    job = build_image_job("A rusty iron key with a worn bow. A single object icon, centered.")
    assert job["prompt"] == "A rusty iron key with a worn bow. A single object icon, centered."
    assert job["workflow_override"]["6"]["inputs"]["text"] == \
        "masterpiece, best quality, A rusty iron key with a worn bow. " \
        "A single object icon, centered."
    assert job["workflow_override"]["5"]["inputs"]["width"] == 1024
    assert job["workflow_override"]["5"]["inputs"]["height"] == 1024


@pytest.mark.parametrize("kind", ["sprite", "scene"])
def test_items_render_through_netayume(kind):
    wf = build_image_job("A key.", kind)["workflow_override"]
    assert wf["4"]["inputs"]["ckpt_name"] == "NetaYume_v4_all_in_one.safetensors"
    # the sampler reads the shifted model, at a cfg where the negative is real
    assert wf["3"]["inputs"]["model"] == ["10", 0]
    assert wf["3"]["inputs"]["cfg"] == 4.5


def test_tiles_render_through_dreamshaper():
    wf = build_image_job("Worn wooden floorboards.", "tile")["workflow_override"]
    assert wf["4"]["inputs"]["ckpt_name"] == "DreamShaperXL_Turbo_v2_1.safetensors"
    assert wf["3"]["inputs"]["model"] == ["4", 0]
    assert wf["5"]["inputs"]["width"] == 1024
    assert wf["5"]["inputs"]["height"] == 1024


def test_a_tile_positive_stays_verbatim():
    """DreamShaperXL is not danbooru-trained; quality tags are off-distribution there."""
    wf = build_image_job("Worn wooden floorboards.", "tile")["workflow_override"]
    assert wf["6"]["inputs"]["text"] == "Worn wooden floorboards."


def test_the_negative_reaches_node_7_and_varies_by_kind():
    item = build_image_job("A key.", "sprite")["workflow_override"]["7"]["inputs"]["text"]
    scene = build_image_job("A key.", "scene")["workflow_override"]["7"]["inputs"]["text"]
    tile = build_image_job("A key.", "tile")["workflow_override"]["7"]["inputs"]["text"]
    assert "photorealistic" in item
    assert scene == item
    # tiles measured 2026-08-06 drift photoreal and grow cracks/objects without the extra terms
    assert tile.startswith(item)
    for term in ("person", "border", "cracks"):
        assert term in tile
    assert "cracks" not in item


def test_the_seed_is_set_per_job():
    wf = build_image_job("A key.")["workflow_override"]
    assert isinstance(wf["3"]["inputs"]["seed"], int)
    assert wf["3"]["inputs"]["seed"] != 0


def test_a_sprite_is_matted():
    wf = build_image_job("A rusty iron key.", "sprite")["workflow_override"]
    assert wf["9"]["inputs"]["images"] == ["47", 0]      # the output reads BiRefNet


@pytest.mark.parametrize("kind", ["tile", "scene"])
def test_a_background_keeps_its_whole_frame(kind):
    """Matting a floor leaves the ragged fragments of a floor that used to be a floor."""
    wf = build_image_job("Worn wooden floorboards.", kind)["workflow_override"]
    assert "47" not in wf
    assert wf["9"]["inputs"]["images"] == ["8", 0]


def test_img2img_job_seeds_from_the_init_image():
    job = build_img2img_job("A rusty iron key, now golden.", "init_abc.png", denoise=0.5)
    wf = job["workflow_override"]
    assert wf["50"]["inputs"]["image"] == "init_abc.png"
    assert wf["3"]["inputs"]["denoise"] == 0.5
    # the sampler denoises the ENCODED init image, not an empty latent
    assert wf["3"]["inputs"]["latent_image"] == ["51", 0]
    assert wf["51"]["inputs"]["pixels"] == ["50", 0]
    assert wf["6"]["inputs"]["text"] == "masterpiece, best quality, A rusty iron key, now golden."


def test_img2img_honours_the_kind_too():
    """A regenerate of a tile must not put the matte back that the first render left out — and it
    must re-render through the tile's own model."""
    wf = build_img2img_job("Worn floorboards.", "init_abc.png", "tile")["workflow_override"]
    assert wf["4"]["inputs"]["ckpt_name"] == "DreamShaperXL_Turbo_v2_1.safetensors"
    assert wf["6"]["inputs"]["text"] == "Worn floorboards."
    assert "47" not in wf
    assert wf["9"]["inputs"]["images"] == ["8", 0]
    assert wf["3"]["inputs"]["latent_image"] == ["51", 0]


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
