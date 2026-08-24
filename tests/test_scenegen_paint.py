import numpy as np
from PIL import Image

from scenegen import paint

TERRAIN = [{"symbol": "G", "name": "grass", "walkable": True, "color": "#4caf50"},
           {"symbol": "W", "name": "water", "walkable": False, "color": "#2196f3"}]
SPEC = {"grass": {"color": "#6da24c", "phrase": "soft green meadow grass"},
        "water": {"color": "#39708f", "phrase": "calm deep water"}}
GRID = ["G" * 8 if r < 4 else "W" * 8 for r in range(6)]


def test_guide_uses_spec_colors_with_bounded_jitter():
    img = paint.guide_image(GRID, TERRAIN, SPEC, cell=8)
    assert img.size == (64, 48)
    a = np.asarray(img).astype(int)
    grass = np.array([0x6d, 0xa2, 0x4c])
    water = np.array([0x39, 0x70, 0x8f])
    assert np.abs(a[:32].mean(axis=(0, 1)) - grass).max() < 6
    assert np.abs(a[32:].mean(axis=(0, 1)) - water).max() < 6
    assert np.abs(a[:32] - grass).max() <= paint.JITTER + 1


def test_region_masks_feather_scales_with_thickness():
    # a 1-cell-thin strip must keep a hard mask; a deep block gets a real feather
    grid = ["G" * 8, "W" * 8, "G" * 8, "G" * 8, "G" * 8, "G" * 8]
    masks = {t["symbol"]: m for t, m in paint.region_masks(grid, TERRAIN, cell=8)}
    thin = np.asarray(masks["W"].convert("L"))
    assert set(np.unique(thin)) <= {0, 255}  # no blur on the thin strip
    deep = np.asarray(masks["G"].convert("L"))
    assert ((deep > 0) & (deep < 255)).any()


def test_regional_payload_has_one_prompt_per_region_and_the_guide():
    payload = paint.regional_payload(GRID, TERRAIN, SPEC, seed=7, cell=8)
    wf = payload["workflow"]
    prompts = [v["inputs"]["text"] for k, v in wf.items() if k.startswith("p")]
    assert any("meadow grass" in p for p in prompts)
    assert any("calm deep water" in p for p in prompts)
    assert wf["k"]["inputs"]["denoise"] == paint.REGIONAL_DENOISE
    assert len(payload["uploads"]) == 3
    assert payload["kind"] == "comfy_image"


def test_blend_payload_carries_the_regional_image():
    import io
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "PNG")
    payload = paint.blend_payload(buf.getvalue(), SPEC, seed=7)
    wf = payload["workflow"]
    assert wf["k"]["inputs"]["denoise"] == paint.BLEND_DENOISE
    assert "meadow grass" in wf["p"]["inputs"]["text"]
    assert len(payload["uploads"]) == 1


def test_absent_terrain_gets_no_mask():
    terrain = TERRAIN + [{"symbol": "X", "name": "lava", "walkable": False,
                          "color": "#c45a28"}]
    masks = paint.region_masks(GRID, terrain, cell=8)
    assert {t["symbol"] for t, _ in masks} == {"G", "W"}
