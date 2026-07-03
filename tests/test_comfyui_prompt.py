import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.comfyui_tools import (_build_character_prompt, build_background_job, build_cg_job,
                                 build_item_job)


def test_uses_natural_language_description():
    pos, _ = _build_character_prompt({
        "id": "mira", "name": "Mira Valen",
        "description": "a lean woman with short cropped black hair streaked teal, "
                       "sharp green eyes, and a weathered olive combat jacket",
    })
    assert "teal" in pos and "combat jacket" in pos
    # style + composition direction must travel with every sprite
    assert "neutral pose" in pos


def test_falls_back_to_name_when_no_description():
    pos, _ = _build_character_prompt({"id": "jax", "name": "Jax Ortega"})
    assert "Jax Ortega" in pos


def test_missing_fields_entirely_does_not_crash():
    pos, neg = _build_character_prompt({})
    assert isinstance(pos, str) and isinstance(neg, str)


def test_no_pony_booru_quality_tags_leak():
    # Anima uses a Qwen text encoder; Pony score_* embeddings are noise.
    pos, neg = _build_character_prompt({"description": "a girl"})
    assert "score_9" not in pos and "score_1" not in neg


def test_background_bakes_description_into_prompt():
    job = build_background_job("A war-scarred plain at twilight with amber backlighting.")
    assert "war-scarred plain" in job["prompt"]
    assert "no humans" in job["prompt"]  # background stays unpopulated
    # backgrounds render on the cel-shaded anime scene model, matching the sprite style
    assert job["workflow_override"]["4"]["inputs"]["ckpt_name"] == "waiIllustriousSDXL_v170.safetensors"


def test_item_job_is_an_icon_not_a_scene():
    job = build_item_job("a rusty iron key")
    assert "rusty iron key" in job["prompt"]
    assert "game item icon" in job["prompt"]
    assert "a single" in job["prompt"] and "plain simple background" in job["prompt"]
    # never the background job's scenery/wide-angle framing
    assert "wide angle background" not in job["prompt"]
    assert "establishing shot" not in job["prompt"]
    wf = job["workflow_override"]
    # square, unlike the widescreen background workflow
    assert wf["5"]["inputs"]["width"] == wf["5"]["inputs"]["height"]
    # the negative must not suppress the foreground object itself
    assert "foreground object" not in wf["7"]["inputs"]["text"]
    assert "scenery" in wf["7"]["inputs"]["text"]


def test_cg_does_not_force_no_characters():
    job = build_cg_job("Mira and Jax stand back to back as the colony collapses.")
    assert "no characters" not in job["prompt"]
    assert "characters in their environment" in job["prompt"]
