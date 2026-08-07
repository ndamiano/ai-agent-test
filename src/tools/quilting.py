"""Efros-Freeman image quilting: synthesize a seamless tile from an AI exemplar.

Patches are chosen by boundary error and stitched along min-error seams; synthesizing on a
torus (all coordinates modulo the output size) makes the result tileable by construction.
"""
import numpy as np
from PIL import Image


def _min_cut_path(err):
    """Dynamic-programming vertical min-cut through an error map (h, w)."""
    h, w = err.shape
    cost = err.copy()
    for i in range(1, h):
        left = np.pad(cost[i-1], (1, 0), constant_values=np.inf)[:-1]
        right = np.pad(cost[i-1], (0, 1), constant_values=np.inf)[1:]
        cost[i] += np.minimum(np.minimum(left, cost[i-1]), right)
    path = np.zeros(h, dtype=int)
    path[-1] = int(np.argmin(cost[-1]))
    for i in range(h - 2, -1, -1):
        j = path[i+1]
        lo, hi = max(0, j-1), min(w, j+2)
        path[i] = lo + int(np.argmin(cost[i, lo:hi]))
    return path


def quilt_tileable(exemplar: np.ndarray, out_size=384, block=96, overlap=24, rng=None):
    """Synthesize an out_size^2 texture on a torus from exemplar (H, W, 3) float32."""
    rng = rng or np.random.default_rng(0)
    H, W, _ = exemplar.shape
    n_blocks = out_size // (block - overlap) + 1
    out = np.zeros((out_size, out_size, 3), dtype=np.float32)
    filled = np.zeros((out_size, out_size), dtype=bool)

    def wrap_get(arr, y, x, h, w):
        ys = (np.arange(y, y+h) % out_size)[:, None]
        xs = (np.arange(x, x+w) % out_size)[None, :]
        return arr[ys, xs]

    def wrap_set(arr, y, x, patch):
        h, w = patch.shape[:2]
        ys = (np.arange(y, y+h) % out_size)[:, None]
        xs = (np.arange(x, x+w) % out_size)[None, :]
        arr[ys, xs] = patch

    n_cand = 60
    for by in range(n_blocks):
        for bx in range(n_blocks):
            y, x = by * (block - overlap), bx * (block - overlap)
            region = wrap_get(out, y, x, block, block)
            mask = wrap_get(filled, y, x, block, block)
            cys = rng.integers(0, H - block, n_cand)
            cxs = rng.integers(0, W - block, n_cand)
            best, best_err = None, np.inf
            for cy, cx in zip(cys, cxs):
                cand = exemplar[cy:cy+block, cx:cx+block]
                if mask.any():
                    e = float((((cand - region) ** 2).sum(-1) * mask).sum() / max(mask.sum(), 1))
                else:
                    e = float(rng.random())
                if e < best_err:
                    best, best_err = cand, e
            patch = best.copy()
            if mask[:, :overlap].any():
                err = ((patch[:, :overlap] - region[:, :overlap]) ** 2).sum(-1)
                cut = _min_cut_path(err)
                for i in range(block):
                    patch[i, :cut[i]] = region[i, :cut[i]]
            if mask[:overlap, :].any():
                err = ((patch[:overlap, :] - region[:overlap, :]) ** 2).sum(-1).T
                cut = _min_cut_path(err)
                for j in range(block):
                    patch[:cut[j], j] = region[:cut[j], j]
            wrap_set(out, y, x, patch)
            wrap_set(filled, y, x, np.ones((block, block), dtype=bool))
    return out


def quilt_tile(src_image: Image.Image, out_size=384, block=96, overlap=24, seed=0) -> Image.Image:
    """A seamless out_size^2 RGB tile quilted from the center of an AI render."""
    im = src_image.convert("RGB")
    w, h = im.size
    # the exemplar is the render's center: the edges are where the sampler drifts
    crop = min(640, w, h)
    left, top = (w - crop) // 2, (h - crop) // 2
    ex = np.asarray(im.crop((left, top, left + crop, top + crop)), dtype=np.float32)
    out = quilt_tileable(ex, out_size=out_size, block=block, overlap=overlap,
                         rng=np.random.default_rng(seed))
    return Image.fromarray(np.uint8(np.clip(out, 0, 255)))
