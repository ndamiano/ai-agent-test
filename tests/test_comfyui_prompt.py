import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.comfyui_tools import build_img2img_item_job, build_item_job, build_item_payload


def test_item_job_is_a_square_icon():
    # The saved styled prompt is appended WHOLE, never embedded mid-phrase — wrapping multi-
    # sentence prose inside "a single {X}, one object only, ..." garbled the grammar and pushed
    # the framing keywords past the CLIP window (subject drift).
    job = build_item_job("A rusty iron key with a worn bow. A single object icon, centered.")
    # the saved styled prompt IS the positive, verbatim — flux reads prose, no tag salting
    assert job["prompt"] == "A rusty iron key with a worn bow. A single object icon, centered."
    wf = job["workflow_override"]
    assert wf["5"]["inputs"]["width"] == wf["5"]["inputs"]["height"]
    # the negative suppresses scenery but must not suppress the foreground object itself
    assert "foreground object" not in wf["7"]["inputs"]["text"]
    assert "scenery" in wf["7"]["inputs"]["text"]


def test_img2img_job_seeds_from_the_init_image():
    job = build_img2img_item_job("A rusty iron key, now golden.", "init_abc.png", denoise=0.5)
    wf = job["workflow_override"]
    assert wf["50"]["inputs"]["image"] == "init_abc.png"
    assert wf["3"]["inputs"]["denoise"] == 0.5
    # the sampler denoises the ENCODED init image, not an empty latent
    assert wf["3"]["inputs"]["latent_image"] == ["51", 0]
    assert wf["51"]["inputs"]["pixels"] == ["50", 0]
    assert wf["6"]["inputs"]["text"] == "A rusty iron key, now golden."


def test_item_payload_with_init_image_carries_the_upload():
    payload = build_item_payload("A rusty iron key.", init_image_b64="aGk=")
    assert payload["kind"] == "comfy_image"
    (up,) = payload["uploads"]
    assert up["b64"] == "aGk="
    # the workflow's LoadImage reads exactly the name the worker will upload under
    assert payload["workflow"]["50"]["inputs"]["image"] == up["name"]


def test_item_payload_without_init_image_has_no_uploads():
    payload = build_item_payload("A rusty iron key.")
    assert "uploads" not in payload
    assert "50" not in payload["workflow"]   # txt2img: empty latent, no LoadImage
