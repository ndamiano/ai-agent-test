"""Quilting: a seamless tile synthesized from an AI render.

The synthesis runs on a torus, so the wrap seam IS an interior seam — that, the fixed output
size, and seeded determinism are the contract the asset chain leans on.
"""

import numpy as np
from PIL import Image, ImageFilter

from tools.quilting import quilt_tile


def _texture(size=160, seed=1):
    """A correlated texture: blurred noise, so adjacent pixels agree and a raw wrap does not."""
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)
    return Image.fromarray(arr).filter(ImageFilter.GaussianBlur(4))


def test_output_is_rgb_at_the_requested_size():
    out = quilt_tile(_texture(), out_size=96, block=32, overlap=8)
    assert out.mode == "RGB"
    assert out.size == (96, 96)


def test_the_same_seed_reproduces_the_same_tile():
    a = quilt_tile(_texture(), out_size=96, block=32, overlap=8, seed=7)
    b = quilt_tile(_texture(), out_size=96, block=32, overlap=8, seed=7)
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_the_tile_wraps_without_a_seam():
    src = _texture()
    out = np.asarray(quilt_tile(src, out_size=96, block=32, overlap=8), dtype=np.float32)
    wrap = 0.5 * (np.abs(out[:, -1] - out[:, 0]).mean() + np.abs(out[-1, :] - out[0, :]).mean())
    interior = 0.5 * (np.abs(np.diff(out, axis=1)).mean() + np.abs(np.diff(out, axis=0)).mean())
    assert wrap < 3 * interior
    # the raw render is the control: its borders never match
    raw = np.asarray(src, dtype=np.float32)
    raw_wrap = 0.5 * (np.abs(raw[:, -1] - raw[:, 0]).mean() + np.abs(raw[-1, :] - raw[0, :]).mean())
    assert wrap < raw_wrap
