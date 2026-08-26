from pathlib import Path

from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.codegen import assets
from maestro.codegen.asset_chain import OPERATIONS


def test_new_asks_answer_webp_and_meshes_stay_glb():
    """One format, no compatibility path — webp is what art is, full stop."""
    assert assets.ext_for("sprite") == "webp"
    assert assets.ext_for("mesh") == "glb"


def test_save_image_writes_webp_at_pipeline_quality(tmp_path):
    im = Image.new("RGBA", (256, 256), (200, 40, 40, 255))
    png, webp = tmp_path / "a.png", tmp_path / "a.webp"
    assets.save_image(im, png)
    assets.save_image(im, webp)
    assert Image.open(webp).format == "WEBP"
    assert Image.open(png).format == "PNG"


def test_save_sprite_converts_to_webp(tmp_path, monkeypatch):
    src = tmp_path / "render.png"
    im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    for x in range(200, 300):
        for y in range(200, 300):
            im.putpixel((x, y), (255, 0, 0, 255))
    im.save(src)
    dst = tmp_path / "hero.webp"
    monkeypatch.setattr("maestro.codegen.asset_chain.asset_path", lambda rid, aid, ext: tmp_path / f"{aid}.{ext}")
    monkeypatch.setattr("maestro.codegen.asset_chain._record_defect", lambda md, d: None)
    monkeypatch.setattr("maestro.codegen.asset_chain._mark_landed", lambda md: None)
    OPERATIONS["save_sprite"]({"run_id": "r", "asset_id": "hero"},
                              {"images": [{"file": str(src), "safety": {"scores": {"NSFW": 0.0, "SFW": 1.0}}}]})
    out = Image.open(dst)
    assert out.format == "WEBP"
    assert out.width < 512          # cropped to the subject plus margin
    assert not src.exists()         # the worker's png does not linger


def test_save_flat_converts_to_webp_without_cropping(tmp_path, monkeypatch):
    src = tmp_path / "render.png"
    Image.new("RGBA", (64, 64), (0, 255, 0, 255)).save(src)
    monkeypatch.setattr("maestro.codegen.asset_chain.asset_path", lambda rid, aid, ext: tmp_path / f"{aid}.{ext}")
    monkeypatch.setattr("maestro.codegen.asset_chain._record_defect", lambda md, d: None)
    monkeypatch.setattr("maestro.codegen.asset_chain._mark_landed", lambda md: None)
    OPERATIONS["save_flat"]({"run_id": "r", "asset_id": "floor"}, {"images": [{"file": str(src), "safety": {"scores": {"NSFW": 0.0, "SFW": 1.0}}}]})
    out = Image.open(tmp_path / "floor.webp")
    assert out.format == "WEBP" and out.size == (64, 64)
    assert not src.exists()
