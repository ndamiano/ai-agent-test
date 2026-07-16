import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.comfyui_tools import build_item_job


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
