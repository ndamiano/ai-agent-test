"""`worker/anim_sheet.py` — pure functions over synthetic frames. Nothing here talks to ComfyUI,
so every rule is testable without a GPU."""

import numpy as np
from PIL import Image, ImageDraw

from worker.anim_sheet import (DIRS, matte, motion_signal, pack, pick_facings,
                               sample, shared_box, trim_pinned)

SIZE = 96


def _blob(cx, cy, w=16, h=28, size=SIZE):
    im = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((cx - w, cy - h, cx + w, cy + h), fill=(30, 30, 30))
    return im


def test_motion_signal_is_zero_on_identical_frames():
    frames = [_blob(48, 48) for _ in range(5)]
    sig = motion_signal(frames)
    assert sig[0] == 0.0
    assert np.allclose(sig, 0.0)


def test_motion_signal_is_positive_when_the_blob_moves():
    frames = [_blob(48, 48), _blob(60, 48)]
    sig = motion_signal(frames)
    assert sig[1] > 0.0


def test_trim_pinned_drops_a_still_head_and_tail():
    moving = [_blob(30 + i * 4, 48) for i in range(6)]
    frames = [moving[0]] * 4 + moving + [moving[-1]] * 4
    trimmed = trim_pinned(frames)
    assert len(trimmed) < len(frames)
    # the moving stretch itself survives, unclipped
    assert len(trimmed) >= len(moving) - 1


def test_trim_pinned_returns_everything_when_nothing_ever_moves():
    frames = [_blob(48, 48) for _ in range(6)]
    assert trim_pinned(frames) == frames


def test_matte_keeps_an_enclosed_white_hole_opaque():
    im = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, 86, 86), fill=(20, 20, 20))
    d.ellipse((40, 40, 56, 56), fill=(255, 255, 255))  # a hole enclosed by the subject
    out = matte(im)
    alpha = np.asarray(out)[:, :, 3]
    assert alpha[48, 48] == 255, "an enclosed white region must stay part of the subject"
    assert alpha[0, 0] == 0, "the background corner must be cut"


def test_matte_makes_the_border_transparent():
    im = _blob(48, 48)
    out = matte(im)
    alpha = np.asarray(out)[:, :, 3]
    assert alpha[2, 2] == 0
    assert alpha[48, 48] == 255


def test_sample_returns_all_frames_when_fewer_than_count():
    frames = [_blob(48, 48) for _ in range(3)]
    assert sample(frames, 8) == frames


def test_sample_spans_first_to_last_evenly():
    frames = [_blob(i, 48) for i in range(20, 40)]
    out = sample(frames, 4)
    assert len(out) == 4
    assert out[0] is frames[0]
    assert out[-1] is frames[-1]


def test_shared_box_unions_and_pads_every_frame():
    a = _blob(30, 48, w=10, h=10)
    b = _blob(70, 48, w=10, h=10)
    box = shared_box([matte(a), matte(b)])
    l, t, r, btm = box
    assert l < 20 and r > 80, "the union must span both subjects"
    assert t < 38 and btm > 58


def _clip(frames, count):
    return [matte(f) for f in sample(frames, count)]


def _lopsided(cx, cy, size=SIZE):
    """front: an asymmetric silhouette (a bump on one side, e.g. holding a weapon)."""
    im = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((cx - 16, cy - 28, cx + 16, cy + 28), fill=(30, 30, 30))
    d.rectangle((cx + 10, cy - 30, cx + 22, cy - 20), fill=(30, 30, 30))
    return im


def _symmetric(cx, cy, size=SIZE):
    """back: a wide, mirror-symmetric silhouette unlike the front."""
    return _blob(cx, cy, w=26, h=20, size=size)


def _turntable_frames(n=40, pin=3):
    """A pinned turntable: the front holds still for `pin` frames at each end; between them
    the silhouette shifts one pixel a frame, so every frame of the turn moves. Returns the
    frames and the index range the true back occupies (the middle of the moving span)."""
    frames = [_lopsided(48, 48) for _ in range(pin)]
    turn = n - 2 * pin
    for i in range(turn):
        frames.append(_lopsided(48 - i, 48))
    frames += [_lopsided(48, 48) for _ in range(pin)]
    mid = pin - 1 + turn // 2
    return frames, mid - 1, mid + 2


def test_pick_facings_front_is_always_frame_zero():
    frames, _, _ = _turntable_frames()
    assert pick_facings(frames)["front"] == 0


def test_pick_facings_back_is_the_middle_of_the_moving_span():
    frames, back_lo, back_hi = _turntable_frames()
    facings = pick_facings(frames)
    assert back_lo <= facings["back"] < back_hi


def test_pick_facings_orders_right_before_back_before_left():
    frames, _, _ = _turntable_frames()
    facings = pick_facings(frames)
    assert facings["right"] < facings["back"] < facings["left"]


def test_pick_facings_ignores_how_long_the_pins_hold():
    short, _, _ = _turntable_frames(n=40, pin=2)
    long, _, _ = _turntable_frames(n=48, pin=6)
    assert pick_facings(short)["back"] - 1 == pick_facings(long)["back"] - 5


def test_pick_facings_on_a_still_clip_spreads_over_the_whole_clip():
    frames = [_lopsided(48, 48) for _ in range(9)]
    assert pick_facings(frames) == {"front": 0, "right": 2, "back": 4, "left": 6}


def test_pick_facings_returns_one_index_per_direction():
    frames, _, _ = _turntable_frames()
    facings = pick_facings(frames)
    assert set(facings) == set(DIRS)


def _clips(anims=("walk", "idle"), dirs=DIRS, frames=4):
    return {(a, d): [matte(_blob(48, 48)) for _ in range(frames)] for a in anims for d in dirs}


def test_pack_with_one_facing_has_one_row_per_anim():
    clips = _clips(anims=("drive", "skid"), dirs=("front",))
    sheet, manifest = pack(clips, {"drive": {"fps": 12}, "skid": {"fps": 12}}, dirs=["front"])
    assert manifest["dirs"] == ["front"]
    assert manifest["anims"]["drive"]["rows"] == {"front": 0}
    assert manifest["anims"]["skid"]["rows"] == {"front": 1}
    assert sheet.height == 2 * manifest["cell"]["h"]


def test_pack_row_order_is_anims_by_dirs():
    clips = _clips(anims=("walk", "idle"))
    sheet, manifest = pack(clips, {"walk": {"fps": 12}, "idle": {"fps": 8}})
    for a in ("walk", "idle"):
        rows = manifest["anims"][a]["rows"]
        assert list(rows) == sorted(rows, key=lambda d: DIRS.index(d))
    walk_rows = set(manifest["anims"]["walk"]["rows"].values())
    idle_rows = set(manifest["anims"]["idle"]["rows"].values())
    assert not (walk_rows & idle_rows), "each (anim, dir) gets its own row"


def test_pack_omits_a_missing_anim_dir_from_the_manifest():
    clips = _clips(anims=("walk",), dirs=("front", "back"))
    _, manifest = pack(clips, {"walk": {"fps": 12}})
    assert set(manifest["anims"]["walk"]["rows"]) == {"front", "back"}
    assert "right" not in manifest["anims"]["walk"]["rows"]
    assert "left" not in manifest["anims"]["walk"]["rows"]


def test_pack_drops_an_anim_with_no_rows_at_all():
    clips = _clips(anims=("walk",), dirs=("front",))
    _, manifest = pack(clips, {"walk": {"fps": 12}, "attack": {"fps": 16}})
    assert "attack" not in manifest["anims"]


def test_pack_cells_are_all_equal_size():
    clips = _clips(anims=("walk", "idle"))
    sheet, manifest = pack(clips, {"walk": {"fps": 12}, "idle": {"fps": 8}})
    cw, ch = manifest["cell"]["w"], manifest["cell"]["h"]
    nrows = len(DIRS) * 2
    assert sheet.height == ch * nrows
    assert sheet.width % cw == 0


def test_pack_pivot_sits_inside_the_cell():
    clips = _clips(anims=("walk",))
    _, manifest = pack(clips, {"walk": {"fps": 12}})
    assert 0 <= manifest["pivot"]["x"] <= manifest["cell"]["w"]
    assert 0 <= manifest["pivot"]["y"] <= manifest["cell"]["h"]


def test_pack_manifest_matches_the_sprites_js_contract():
    clips = _clips(anims=("walk", "idle"))
    _, manifest = pack(clips, {"walk": {"fps": 12}, "idle": {"fps": 8}})
    assert set(manifest) == {"cell", "dirs", "anims", "pivot", "warnings"}
    assert set(manifest["cell"]) == {"w", "h"}
    assert manifest["dirs"] == list(DIRS)
    for a, spec in manifest["anims"].items():
        assert set(spec) == {"rows", "frames", "fps"}
    assert manifest["warnings"] == []


def test_pack_carries_forward_the_given_warnings():
    clips = _clips(anims=("walk",))
    _, manifest = pack(clips, {"walk": {"fps": 12}}, warnings=["walk/front: weak motion"])
    assert manifest["warnings"] == ["walk/front: weak motion"]
