import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.ir_assemble import assemble_ir, used_emotions, expression_file
from renpy.ir_vn import compile_vn
from renpy.component_schemas import SCHEMAS
from tools.comfyui_tools import build_character_emotion_job
from utils.image import write_solid_png


def _artifact():
    return {
        "premise": {"characters": [{"id": "al", "name": "Al"}, {"id": "bo", "name": "Bo"}]},
        "asset_manifest": {"characters": [
            {"id": "al", "image_file": "al.png"},
            {"id": "bo", "image_file": "bo.png"},
        ]},
        "nodes": {"node_ids": ["n1"], "start": "n1", "nodes": {"n1": {
            "location": None,
            "lines": [
                {"speaker": "al", "text": "x", "emotion": "happy"},
                {"speaker": "al", "text": "y", "emotion": "angry"},
                {"speaker": "bo", "text": "z"},  # no emotion -> neutral
            ],
            "end": {"type": "end"},
        }}},
    }


# --- ir_assemble: expressions derived only for emotions actually used --------------------------

def test_expression_file_convention():
    assert expression_file("al.png", "neutral") == "al.png"       # neutral keeps original
    assert expression_file("al.png", "happy") == "al_happy.png"   # variants get a sibling


def test_used_emotions_scans_only_that_speaker():
    nodes = [{"id": "n1", **_artifact()["nodes"]["nodes"]["n1"]}]
    assert used_emotions("al", nodes) == ["neutral", "happy", "angry"]  # EMOTIONS order
    assert used_emotions("bo", nodes) == ["neutral"]                    # only ever neutral


def test_assemble_emits_expressions_for_used_only():
    ir = assemble_ir(_artifact(), "vn")
    al = next(c for c in ir["characters"] if c["id"] == "al")
    bo = next(c for c in ir["characters"] if c["id"] == "bo")
    assert al["sprite"] == "al.png"
    assert al["expressions"] == {"neutral": "al.png", "happy": "al_happy.png",
                                 "angry": "al_angry.png"}
    # bo only ever speaks neutral -> no variants generated, no expressions map
    assert bo["sprite"] == "bo.png"
    assert "expressions" not in bo


# --- ir_vn: projection to per-expression shows with a stable as-tag ----------------------------

def test_emotion_variants_declared():
    out = compile_vn(assemble_ir(_artifact(), "vn"))
    assert 'image char_al_happy:\n    "images/al_happy.png"\n    zoom 0.55' in out
    assert 'image char_al_angry:\n    "images/al_angry.png"\n    zoom 0.55' in out
    assert 'image char_al_neutral:\n    "images/al.png"\n    zoom 0.55' in out
    assert 'image char_bo_neutral:\n    "images/bo.png"\n    zoom 0.55' in out


def test_emotion_swaps_in_place_via_stable_tag():
    out = compile_vn(assemble_ir(_artifact(), "vn"))
    # al's expression swaps happy -> angry, always under the same `as char_al` tag
    assert "show char_al_happy as char_al at stage(0.3333), speaking" in out
    assert "show char_al_angry as char_al at stage(0.3333), speaking" in out
    # bo speaks neutral and is brightened; al holds his last expression (angry) and is dimmed
    assert "show char_bo_neutral as char_bo at stage(0.6667), speaking" in out
    assert "show char_al_angry as char_al at stage(0.3333), not_speaking" in out


def test_missing_emotion_falls_back_to_neutral():
    # A line emotion the character has no variant for must not dangle a missing image.
    ir = {
        "version": "0.1", "genre": "visual_novel", "start": {"node": "n1"},
        "characters": [{"id": "al", "name": "Al", "sprite": "al.png",
                        "expressions": {"neutral": "al.png"}}],
        "nodes": [{"id": "n1", "lines": [{"speaker": "al", "text": "x", "emotion": "sad"}],
                   "end": {"type": "end"}}],
    }
    out = compile_vn(ir)
    assert "char_al_sad" not in out
    assert "show char_al_neutral as char_al" in out


# --- comfyui img2img job builder --------------------------------------------------------------

def test_emotion_job_is_low_denoise_img2img_off_base():
    job = build_character_emotion_job({"description": "a tall knight in armor"},
                                      "angry", "al.png")
    wf = job["workflow_override"]
    assert 0 < wf["19"]["inputs"]["denoise"] < 1          # img2img, not full txt2img
    assert wf["28"]["class_type"] == "LoadImage"
    assert wf["28"]["inputs"]["image"] == "al.png"        # seeded from the uploaded neutral
    assert wf["29"]["class_type"] == "VAEEncode"
    assert "scowling" in job["prompt"]                    # the angry expression clause


# --- write-time gate rejects an out-of-enum emotion -------------------------------------------

def test_nodes_validator_rejects_bad_emotion():
    bad = {"node_ids": ["n1"], "nodes": {"n1": {
        "lines": [{"speaker": "al", "text": "x", "emotion": "smug"}],
        "end": {"type": "end"}}}}
    assert SCHEMAS["nodes"](bad)  # returns an error string


# --- fns two-pass: neutral base then img2img expression variants -------------------------------

def test_generate_images_two_pass_builds_variants_off_neutral(tmp_path, monkeypatch):
    import tools.comfyui_tools as ct
    from renpy.fns import generate_images

    src = tmp_path / "src.png"
    write_solid_png(src, 64, 64, (10, 20, 30))

    calls = {"uploads": [], "passes": []}
    monkeypatch.setattr(ct, "upload_image",
                        lambda p, endpoint=None: calls["uploads"].append(p) or "uploaded.png")

    def fake_run(jobs):
        calls["passes"].append(len(jobs))
        return [{"success": True, "saved_paths": [str(src)]} for _ in jobs]
    monkeypatch.setattr(ct, "run_jobs", fake_run)

    inputs = {
        "premise": {"characters": [{"id": "al", "name": "Al"}]},
        "asset_manifest": {"characters": [{"id": "al", "image_file": "al.png"}]},
        "nodes": {"node_ids": ["n1"], "nodes": {"n1": {
            "lines": [{"speaker": "al", "text": "x", "emotion": "happy"}],
            "end": {"type": "end"}}}},
    }
    res = generate_images(inputs, tmp_path)

    images = tmp_path / "game_output" / "game" / "images"
    assert (images / "al.png").exists()         # neutral base (pass 1)
    assert (images / "al_happy.png").exists()   # img2img variant (pass 2)
    assert {"al.png", "al_happy.png"} <= set(res["generated"])
    assert calls["passes"] == [1, 1]            # one base job, then one emotion job
    assert calls["uploads"] == [str(images / "al.png")]  # neutral uploaded to seed img2img
