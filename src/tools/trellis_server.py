"""Standalone TRELLIS.2 HTTP server — run BY the TRELLIS venv (NOT the maestro venv), a long-lived
process maestro POSTs sprites to (like ComfyUI). Replaces the per-build subprocess batch: the 4B
pipeline loads ONCE and stays resident across builds instead of reloading every run.

TRELLIS.2-4B is image->mesh WITH native PBR textures (base colour / roughness / metallic), so its
meshes drop into the HD-2D world already coloured. It needs its own cu128 venv + prebuilt Blackwell
CUDA-extension wheels, so it can't share maestro's process — hence a separate server on its own port.

Launch (from the TRELLIS venv):
  <trellis_python> trellis_server.py --repo <trellis2 repo> --weights <weights dir>
                                     [--host 127.0.0.1] [--port 8189]
                                     [--ptype 1024_cascade] [--texture 2048]

API:
  GET  /health            -> {"status": "ok", "loaded": <bool>}
  POST /generate          body = PNG bytes (image/png); query ?ptype=&texture= override defaults
                          -> 200 model/gltf-binary (the .glb bytes) | 500 on failure
"""
# ruff: noqa: PLC0415 — every heavy import here is deliberately deferred: this module runs under
# the TRELLIS venv (torch / trellis2 / o_voxel / flex_gemm are absent from maestro's), and the
# Blackwell patches must be applied around the torch import, not at module scope.
import argparse
import io
import os
import sys
import tempfile
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
    import trellis2.modules.attention.config as ac
    import trellis2.modules.sparse.config as sc
    sc.ATTN = "sdpa"
    ac.BACKEND = "sdpa"


class TrellisEngine:
    """Holds the 4B pipeline, lazy-loaded on first generate and resident for the process lifetime —
    this server owns its GPU."""

    def __init__(self, repo: str, weights: str, ptype: str, texture: int):
        self.repo, self.weights = repo, weights
        self.ptype, self.texture = ptype, texture
        self._pipe = None
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        os.environ.setdefault("SPCONV_ALGO", "native")
        sys.path.insert(0, repo)
        _blackwell_patches()

    @property
    def loaded(self) -> bool:
        return self._pipe is not None

    def _ensure(self):
        if self._pipe is not None:
            return
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        print("[trellis] loading pipeline…", flush=True)
        t0 = time.time()
        pipe = Trellis2ImageTo3DPipeline.from_pretrained(self.weights)
        t1 = time.time()
        pipe.cuda()
        t2 = time.time()
        self._pipe = pipe
        # The split says WHERE a slow cold start goes: from_pretrained = reading weights off the
        # volume (mmap page faults may defer some of that into cuda()); cuda() = host->device copy.
        print(f"[trellis] loaded in {t2 - t0:.1f}s "
              f"(from_pretrained {t1 - t0:.1f}s, cuda {t2 - t1:.1f}s)", flush=True)

    def generate(self, png_bytes: bytes, ptype: str, texture: int) -> bytes:
        from PIL import Image
        self._ensure()
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        try:
            return self._generate(img, ptype, texture, decimation=500000)
        except Exception as e:
            if "out of memory" not in str(e).lower():
                raise
            # The voxel RESOLUTION is the memory driver (1024_cascade OOM'd CuMesh even at
            # texture 1024 on a fresh 32GB card). Degrade THAT image once — the 512 tier +
            # small texture — instead of failing it outright.
            print(f"[trellis] OOM at ptype={ptype} texture={texture}; retrying degraded",
                  flush=True)
            return self._generate(img, "512", min(texture, 512), decimation=250000)

    def _generate(self, img, ptype: str, texture: int, decimation: int) -> bytes:
        import gc

        import o_voxel
        import torch
        mesh = glb = None
        try:
            with torch.inference_mode():
                mesh = self._pipe.run(img, pipeline_type=ptype)[0]
                mesh.simplify(16777216)
                glb = o_voxel.postprocess.to_glb(
                    vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
                    coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                    aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]], decimation_target=decimation,
                    texture_size=texture, remesh=True, remesh_band=1, remesh_project=0,
                    verbose=False)
            with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as f:
                tmp = f.name
            try:
                glb.export(tmp, extension_webp=True)
                with open(tmp, "rb") as fh:
                    return fh.read()
            finally:
                os.unlink(tmp)
        finally:
            # Two allocators share this card: torch's caching allocator hoards freed blocks it
            # never returns to CUDA, and CuMesh/o_voxel allocate RAW CUDA memory outside torch —
            # across a resident batch torch's cache grew until CuMesh OOM'd (observed live at
            # ~30 meshes, 70s->160s/mesh). Drop the per-generate refs and give the cache back.
            del mesh, glb
            gc.collect()
            torch.cuda.empty_cache()


def build_app(engine: TrellisEngine):
    from fastapi import FastAPI, HTTPException, Request, Response

    app = FastAPI(title="trellis2")

    @app.get("/health")
    def health():
        return {"status": "ok", "loaded": engine.loaded}

    @app.post("/generate")
    async def generate(request: Request):
        png = await request.body()
        if not png:
            raise HTTPException(status_code=400, detail="empty body (expected PNG bytes)")
        ptype = request.query_params.get("ptype", engine.ptype)
        texture = int(request.query_params.get("texture", engine.texture))
        try:
            t = time.time()
            glb = engine.generate(png, ptype, texture)
            print(f"[trellis] ok {len(glb)} bytes {time.time() - t:.1f}s", flush=True)
            return Response(content=glb, media_type="model/gltf-binary")
        except Exception as e:
            print(f"[trellis] FAIL: {str(e)[:200]}", flush=True)
            raise HTTPException(status_code=500, detail=str(e)[:200])

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8189)
    # Feature/prop objects render 1-3 world units tall under a low chibi camera: the 512 voxel
    # tier + a 1K texture is visually indistinguishable there, ~3x faster, and fits CuMesh's
    # post-processing beside the resident pipeline (1024_cascade + 2K OOM'd a 32GB card).
    ap.add_argument("--ptype", default="512")
    ap.add_argument("--texture", type=int, default=1024)
    args = ap.parse_args()

    import uvicorn
    engine = TrellisEngine(args.repo, args.weights, args.ptype, args.texture)
    print(f"[trellis] serving on {args.host}:{args.port}", flush=True)
    uvicorn.run(build_app(engine), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
