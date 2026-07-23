"""The TRELLIS server's model-subset contract.

No torch here: the module keeps every heavy import inside a function, so the selection logic is
testable in the plain maestro venv.
"""
import json

import pytest

from tools.trellis_server import (_ALWAYS_LOAD, _TIER_MODELS, models_for, stage_weights,
                                  weight_files)


def _weights_dir(tmp_path, sizes):
    (tmp_path / "ckpts").mkdir(parents=True)
    models = {}
    for name, (stem, size) in sizes.items():
        models[name] = f"ckpts/{stem}"
        (tmp_path / "ckpts" / f"{stem}.safetensors").write_bytes(b"x" * size)
    (tmp_path / "pipeline.json").write_text(json.dumps({"args": {"models": models}}))
    return tmp_path


def test_512_subset_skips_the_1024_tier():
    names = models_for({"512"})
    assert set(_ALWAYS_LOAD) <= set(names)
    assert "shape_slat_flow_model_512" in names
    assert "tex_slat_flow_model_512" in names
    assert not [n for n in names if "1024" in n]


def test_cascade_carries_both_tiers():
    names = models_for({"1024_cascade", "512"})
    for expected in ("shape_slat_flow_model_512", "shape_slat_flow_model_1024",
                     "tex_slat_flow_model_1024", "tex_slat_flow_model_512"):
        assert expected in names


@pytest.mark.parametrize("tier", sorted(_TIER_MODELS))
def test_every_tier_loads_what_its_pipeline_asserts(tier):
    """run() asserts these names are present for its pipeline_type; a subset missing one would
    fail only at generate time, on a pod, inside a billed job."""
    assert set(_TIER_MODELS[tier]) <= set(models_for({tier}))


def test_names_are_sorted_and_unique():
    names = models_for({"512", "1024_cascade"})
    assert names == sorted(set(names))


def test_weight_files_resolves_only_the_requested_models(tmp_path):
    w = _weights_dir(tmp_path, {
        "shape_slat_flow_model_512": ("shape_512", 16),
        "shape_slat_flow_model_1024": ("shape_1024", 16),
    })
    paths = weight_files(str(w), ["shape_slat_flow_model_512"])
    assert [p.rsplit("/", 1)[-1] for p in paths] == ["shape_512.safetensors"]


def test_weight_files_skips_models_with_no_local_checkpoint(tmp_path):
    """pipeline.json names the sparse-structure decoder by hub id, which resolves out of the HF
    cache rather than ckpts/ — prefaulting must not choke on it."""
    w = _weights_dir(tmp_path, {"shape_slat_flow_model_512": ("shape_512", 16)})
    cfg = json.loads((w / "pipeline.json").read_text())
    cfg["args"]["models"]["sparse_structure_decoder"] = "microsoft/TRELLIS-image-large/ckpts/ss"
    (w / "pipeline.json").write_text(json.dumps(cfg))

    paths = weight_files(str(w), ["shape_slat_flow_model_512", "sparse_structure_decoder",
                                  "not_in_the_config"])
    assert [p.rsplit("/", 1)[-1] for p in paths] == ["shape_512.safetensors"]


def test_stage_weights_copies_checkpoints_and_their_sidecar_configs(tmp_path):
    w = _weights_dir(tmp_path / "vol", {"shape_slat_flow_model_512": ("shape_512", 4096)})
    (w / "ckpts" / "shape_512.json").write_text('{"name": "SLatFlowModel", "args": {}}')
    stage = tmp_path / "stage"

    path, seconds = stage_weights(str(w), ["shape_slat_flow_model_512"], str(stage))

    assert path == str(stage) and seconds > 0
    assert (stage / "ckpts" / "shape_512.safetensors").read_bytes() == b"x" * 4096
    assert (stage / "ckpts" / "shape_512.json").exists(), "loader needs the sidecar config"
    assert (stage / "pipeline.json").exists()


def test_stage_weights_falls_back_to_the_volume_when_it_will_not_fit(tmp_path, monkeypatch):
    """A pod that cannot stage must still serve — slowly — rather than fail to boot."""
    import shutil as _shutil
    w = _weights_dir(tmp_path / "vol", {"shape_slat_flow_model_512": ("shape_512", 4096)})
    monkeypatch.setattr(_shutil, "disk_usage",
                        lambda p: _shutil._ntuple_diskusage(total=1, used=1, free=1))

    path, seconds = stage_weights(str(w), ["shape_slat_flow_model_512"],
                                  str(tmp_path / "stage"))

    assert path == str(w) and seconds == 0.0
