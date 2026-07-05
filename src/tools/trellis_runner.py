"""Standalone TRELLIS.2 batch runner — executed by the TRELLIS venv (NOT the maestro venv),
subprocess-invoked from tools.comfyui_tools.run_trellis_batch.

TRELLIS.2-4B is image->mesh WITH native PBR textures (base colour / roughness / metallic),
so its meshes drop into the HD-2D world already coloured — no sprite-projection hack. It runs
standalone (its own cu128 venv + prebuilt Blackwell CUDA-extension wheels), so maestro shells
out to it rather than hosting it in-process. Loads the 4B pipeline ONCE and loops the sprites.

Invoked as:
  <trellis_python> trellis_runner.py --repo <trellis2 repo> --weights <dir>
                                     --sprites <in dir> --out <glb out dir> [--ptype 1024_cascade]
Writes <out>/<slug>.glb per input <slug>.png and <out>/timings.json (slug -> seconds).
"""
import argparse
import glob
import json
import os
import sys
import time


def _blackwell_patches():
    """The sm_120 fixes discovered live: with triton 3.5+ (torch 2.9) Triton emits sm_120
    natively, so do NOT force get_device_capability -> (9,0). flex_gemm's Triton
    indice-weighted-sum can't compile for CC>=10, so swap in a pure-torch fallback; attention
    runs on sdpa (no flash-attn built)."""
    import torch
    try:
        import flex_gemm.kernels.triton as _fgk

        def _fwd(feats, indices, weight):
            idx = indices.long().clamp(0, feats.shape[0] - 1)
            return (feats[idx] * weight.unsqueeze(-1)).sum(dim=1)

        def _bwd(grad, indices, weight, N):
            _, C = grad.shape
            idx = indices.long().clamp(0, N - 1)
            wg = grad.unsqueeze(1) * weight.unsqueeze(-1)
            gf = torch.zeros(N, C, device=grad.device, dtype=grad.dtype)
            gf.scatter_add_(0, idx.unsqueeze(-1).expand_as(wg).reshape(-1, C), wg.reshape(-1, C))
            return gf

        _fgk.indice_weighed_sum_fwd = _fwd
        _fgk.indice_weighed_sum_bwd_input = _bwd
    except Exception as e:
        print(f"[trellis] flex_gemm patch skipped: {e}", flush=True)
    import trellis2.modules.sparse.config as sc
    import trellis2.modules.attention.config as ac
    sc.ATTN = "sdpa"
    ac.BACKEND = "sdpa"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--sprites", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ptype", default="1024_cascade")
    ap.add_argument("--texture", type=int, default=2048)
    args = ap.parse_args()

    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    os.environ.setdefault("SPCONV_ALGO", "native")
    sys.path.insert(0, args.repo)

    _blackwell_patches()
    from PIL import Image
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    import o_voxel

    os.makedirs(args.out, exist_ok=True)
    sprites = sorted(glob.glob(os.path.join(args.sprites, "*.png")))
    if not sprites:
        print("[trellis] no sprites", flush=True)
        return
    print(f"[trellis] loading pipeline ({len(sprites)} sprites)…", flush=True)
    t0 = time.time()
    pipe = Trellis2ImageTo3DPipeline.from_pretrained(args.weights)
    pipe.cuda()
    print(f"[trellis] loaded in {time.time() - t0:.1f}s", flush=True)

    timings = {}
    for png in sprites:
        slug = os.path.splitext(os.path.basename(png))[0]
        try:
            img = Image.open(png).convert("RGBA")
            t = time.time()
            mesh = pipe.run(img, pipeline_type=args.ptype)[0]
            mesh.simplify(16777216)
            glb = o_voxel.postprocess.to_glb(
                vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
                coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]], decimation_target=1000000,
                texture_size=args.texture, remesh=True, remesh_band=1, remesh_project=0,
                verbose=False)
            glb.export(os.path.join(args.out, f"{slug}.glb"), extension_webp=True)
            timings[slug] = round(time.time() - t, 1)
            print(f"[trellis] ok {slug} {timings[slug]}s", flush=True)
        except Exception as e:
            print(f"[trellis] FAIL {slug}: {str(e)[:160]}", flush=True)
    json.dump(timings, open(os.path.join(args.out, "timings.json"), "w"), indent=1)
    print("[trellis] batch done", flush=True)


if __name__ == "__main__":
    main()
