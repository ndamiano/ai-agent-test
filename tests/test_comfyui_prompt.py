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


def test_background_bakes_description_into_prompt():
    job = build_background_job("A war-scarred plain at twilight with amber backlighting.")
    assert "war-scarred plain" in job["prompt"]
    assert "no humans" in job["prompt"]  # background stays unpopulated


def test_item_job_is_a_square_icon():
    # The saved styled prompt is appended WHOLE, never embedded mid-phrase — wrapping multi-
    # sentence prose inside "a single {X}, one object only, ..." garbled the grammar and pushed
    # the framing keywords past the CLIP window (subject drift).
    job = build_item_job("A rusty iron key with a worn bow. A single object icon, centered.")
    # the saved styled prompt IS the positive, verbatim — flux reads prose, no tag salting
    assert job["prompt"] == "A rusty iron key with a worn bow. A single object icon, centered."
    wf = job["workflow_override"]
    # square, unlike the widescreen background workflow
    assert wf["5"]["inputs"]["width"] == wf["5"]["inputs"]["height"]
    # the negative suppresses scenery but must not suppress the foreground object itself
    assert "foreground object" not in wf["7"]["inputs"]["text"]
    assert "scenery" in wf["7"]["inputs"]["text"]


def test_cg_prompt_places_characters_in_environment():
    job = build_cg_job("Mira and Jax stand back to back as the colony collapses.")
    assert "characters in their environment" in job["prompt"]
