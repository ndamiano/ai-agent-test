"""Standalone TRELLIS.2 HTTP server — run BY the TRELLIS venv (NOT the maestro venv), a long-lived
process maestro POSTs sprites to (like ComfyUI): the 4B pipeline loads ONCE and stays resident
across builds.

TRELLIS.2-4B is image->mesh WITH native PBR textures (base colour / roughness / metallic), so its
meshes drop into the HD-2D world already coloured. It needs its own cu128 venv + prebuilt Blackwell
CUDA-extension wheels, so it can't share maestro's process — hence a separate server on its own port.

Launch (from the TRELLIS venv):
  <trellis_python> trellis_server.py --repo <trellis2 repo> --weights <weights dir>
                                     [--host 127.0.0.1] [--port 8189]
                                     [--ptype 1024_cascade] [--texture 2048]

API:
  GET  /health            -> {"status": "ok", "loaded": <bool>, "warm": <bool>, + the boot
                             timing split (stage / load / warmup seconds). The entrypoint gates
                             worker registration on `warm`, so a claimed job never pays boot.
  POST /generate          body = PNG bytes (image/png); query ?ptype=&texture= override defaults
                          -> 200 model/gltf-binary (the .glb bytes) | 500 on failure
"""
# ruff: noqa: PLC0415 — every heavy import here is deliberately deferred: this module runs under
# the TRELLIS venv (torch / trellis2 / o_voxel / flex_gemm are absent from maestro's), and the
# Blackwell patches must be applied around the torch import, not at module scope.
import argparse
import contextlib
import io
import os
import sys
import tempfile
import threading
import time

# What run() asserts each pipeline type needs (trellis2_image_to_3d.py). Loading the whole set
# costs a 1.3B DiT of construct + 2.6GB of read per unused tier.
_ALWAYS_LOAD = ("sparse_structure_flow_model", "sparse_structure_decoder",
                "shape_slat_decoder", "tex_slat_decoder")
_TIER_MODELS = {
    "512": ("shape_slat_flow_model_512", "tex_slat_flow_model_512"),
    "1024": ("shape_slat_flow_model_1024", "tex_slat_flow_model_1024"),
    "1024_cascade": ("shape_slat_flow_model_512", "shape_slat_flow_model_1024",
                     "tex_slat_flow_model_1024"),
    "1536_cascade": ("shape_slat_flow_model_512", "shape_slat_flow_model_1024",
                     "tex_slat_flow_model_1024"),
}


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


def weight_files(weights: str, names) -> list:
    """The checkpoint file behind each pipeline model name, via pipeline.json's own mapping."""
    import json
    with open(os.path.join(weights, "pipeline.json")) as f:
        models = json.load(f)["args"]["models"]
    paths = []
    for name in names:
        rel = models.get(name)
        if not rel:
            continue
        path = os.path.join(weights, f"{rel}.safetensors")
        if os.path.exists(path):
            paths.append(path)
    return paths


def stage_weights(weights: str, names, stage_root: str, streams: int = 8):
    """Copy everything the pipeline will read onto tmpfs and load from THERE.

    Measured on a pod: bulk-reading the same files streams at 2.7GB/s, yet the load right after
    still took 47.8s — the FUSE mount does not keep the pages, so safetensors' mmap re-reads
    every byte at ~200MB/s. Reading once into RAM and loading from RAM is the only version of
    this that the filesystem cannot undo.

    That covers the checkpoints AND the encoders (DINOv3, BiRefNet), which read through
    transformers at pipeline construct off the same mount; the staged pipeline configs are
    rewritten to the staged copies. Two things are deliberately NOT staged: the hub cache
    (nothing this pipeline serves reads it — CLIP is trainer-only code, and DINOv3 loads from
    the encoders dir the configs name) and the triton kernel cache (its .so files are dlopen'd,
    and tmpfs mounts are noexec — a staged copy fails to load with "failed to map segment").

    Returns (path_to_load_from, seconds). Falls back to the volume when the copy will not fit or
    fails — per part: a slow pod beats a dead one.
    """
    if not stage_root:
        return weights, 0.0
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    def tree_files(root):
        return [os.path.join(r, f) for r, _, fs in os.walk(root) for f in fs]

    def copy_tree(files, src_root, dst_root, pool):
        def one(src):
            dst = os.path.join(dst_root, os.path.relpath(src, src_root))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        list(pool.map(one, files))

    paths = weight_files(weights, names)
    enc_src = os.path.join(os.path.dirname(weights), "encoders")
    lazy = [(enc_src, os.path.join(stage_root, "encoders"))] if os.path.isdir(enc_src) else []
    need = sum(os.path.getsize(p) for p in paths)
    t0 = time.time()
    try:
        os.makedirs(os.path.join(stage_root, "ckpts"), exist_ok=True)
        free = shutil.disk_usage(stage_root).free
        # Headroom: the loader also builds fp32 tensors while these files are resident.
        if free < need * 1.15:
            print(f"[trellis] staging skipped: {free / 1e9:.1f} GB free at {stage_root}, "
                  f"need {need * 1.15 / 1e9:.1f} GB — loading off the volume", flush=True)
            return weights, 0.0

        with ThreadPoolExecutor(max_workers=streams) as pool:
            def copy(src):
                rel = os.path.relpath(src, weights)
                shutil.copyfile(src, os.path.join(stage_root, rel))
                # Each checkpoint's sidecar config is what names its class — tiny, and the loader
                # refuses the file without it.
                cfg = f"{os.path.splitext(src)[0]}.json"
                if os.path.exists(cfg):
                    shutil.copyfile(cfg, os.path.join(
                        stage_root, os.path.relpath(cfg, weights)))

            list(pool.map(copy, paths))
            for top in ("pipeline.json", "texturing_pipeline.json"):
                src = os.path.join(weights, top)
                if os.path.exists(src):
                    shutil.copyfile(src, os.path.join(stage_root, top))
    except OSError as e:
        print(f"[trellis] staging failed ({e}) — loading off the volume", flush=True)
        return weights, 0.0

    ckpt_gb = need
    try:
        lazy_files = [(files, src, dst) for src, dst in lazy
                      if (files := tree_files(src))]
        lazy_need = sum(os.path.getsize(f) for files, _, _ in lazy_files for f in files)
        if shutil.disk_usage(stage_root).free < lazy_need * 1.15:
            raise OSError(f"{lazy_need / 1e9:.1f} GB of encoders won't fit")
        with ThreadPoolExecutor(max_workers=streams) as pool:
            for files, src, dst in lazy_files:
                copy_tree(files, src, dst, pool)
        for top in ("pipeline.json", "texturing_pipeline.json"):
            p = os.path.join(stage_root, top)
            if os.path.exists(p):
                with open(p) as f:
                    body = f.read()
                with open(p, "w") as f:
                    f.write(body.replace(enc_src, os.path.join(stage_root, "encoders")))
        need += lazy_need
    except OSError as e:
        # The ckpt stage above already landed whole — keep it. Configs are unrewritten and
        # the env untouched, so the lazy half simply loads off the volume as before.
        print(f"[trellis] encoder staging skipped ({e}) — encoders load off the volume",
              flush=True)

    dt = time.time() - t0
    print(f"[trellis] staged {need / 1e9:.1f} GB ({ckpt_gb / 1e9:.1f} ckpts) to {stage_root} "
          f"in {dt:.1f}s ({need / dt / 1e6:.0f} MB/s, {streams} streams)", flush=True)
    return stage_root, dt


def models_for(tiers) -> list:
    """The model names a process serving `tiers` must load — everything else is a 1.3B DiT's
    worth of construct and 2.6GB of read for a tier no request can ask for."""
    names = set(_ALWAYS_LOAD)
    for tier in tiers:
        names.update(_TIER_MODELS[tier])
    return sorted(names)


@contextlib.contextmanager
def _skip_default_init():
    """Constructing the pipeline default-inits every weight the checkpoint overwrites
    microseconds later: measured 37s of a 43s load, and it scales with the pod's CPU, not its
    GPU — the single largest term in a cold start. Allocation and structure are untouched. The
    one tensor no checkpoint carries (ss_flow's rope_phases) is computed in __init__ rather than
    initialised, so it comes out bit-identical."""
    import torch.nn as nn
    inits = [n for n in dir(nn.init) if n.endswith("_") and not n.startswith("_")]
    resettable = [c for c in vars(nn).values()
                  if isinstance(c, type) and issubclass(c, nn.Module)
                  and "reset_parameters" in vars(c)]
    saved_init = {n: getattr(nn.init, n) for n in inits}
    saved_reset = {c: c.reset_parameters for c in resettable}
    for n in inits:
        setattr(nn.init, n, lambda t, *a, **k: t)
    for c in resettable:
        c.reset_parameters = lambda self: None
    try:
        yield
    finally:
        for n, fn in saved_init.items():
            setattr(nn.init, n, fn)
        for c, fn in saved_reset.items():
            c.reset_parameters = fn


def _patch_model_loader():
    """Wrap TRELLIS's own checkpoint loader only — transformers models (DINOv3, BiRefNet) build
    through their own path and keep stock init."""
    import trellis2.models as tmodels
    original = tmodels.from_pretrained

    def from_pretrained(path, **kwargs):
        with _skip_default_init():
            return original(path, **kwargs)

    tmodels.from_pretrained = from_pretrained


class TrellisEngine:
    """Holds the 4B pipeline, lazy-loaded on first generate and resident for the process lifetime —
    this server owns its GPU."""

    def __init__(self, repo: str, weights: str, ptype: str, texture: int):
        self.repo, self.weights = repo, weights
        self.ptype, self.texture = ptype, texture
        self._pipe = None
        self._lock = threading.Lock()
        # Ride out on every generate: a pod's stdout is not reachable, so this is the only way
        # cold-start attribution survives into the jobs table.
        self.load_seconds = None
        self.stage_seconds = None
        self.warmup_seconds = None
        self.warmup_error = None
        self.warmed = False
        # Where the checkpoints are read from: the volume, or the tmpfs copy staged at boot.
        self.load_from = weights
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        os.environ.setdefault("SPCONV_ALGO", "native")
        sys.path.insert(0, repo)
        _blackwell_patches()
        _patch_model_loader()

    @property
    def loaded(self) -> bool:
        return self._pipe is not None

    @property
    def tiers(self) -> set:
        """The pipeline types this process can serve: the configured one plus 512, which the OOM
        path degrades to."""
        return {self.ptype, "512"}

    def _ensure(self):
        with self._lock:
            if self._pipe is not None:
                return
            from trellis2.pipelines import Trellis2ImageTo3DPipeline
            names = models_for(self.tiers)
            Trellis2ImageTo3DPipeline.model_names_to_load = names
            print(f"[trellis] loading pipeline… ({len(names)} models for {sorted(self.tiers)})",
                  flush=True)
            t0 = time.time()
            pipe = Trellis2ImageTo3DPipeline.from_pretrained(self.load_from)
            t1 = time.time()
            pipe.cuda()
            t2 = time.time()
            self._pipe = pipe
            self.load_seconds = round(t2 - t0, 1)
            # The split says WHERE a slow cold start goes: from_pretrained = construct + read off
            # the volume; cuda() is ~0 because this pipeline moves models per stage.
            print(f"[trellis] loaded in {t2 - t0:.1f}s "
                  f"(from_pretrained {t1 - t0:.1f}s, cuda {t2 - t1:.1f}s)", flush=True)

    @contextlib.contextmanager
    def _stage_timers(self):
        """Print how long each pipeline stage takes while the wrapped block runs. One-time costs
        hide between the sampler progress bars (a 33s flex_gemm autotune lived there unseen);
        only a per-stage split says which stage owns the time."""
        stages = ("preprocess_image", "get_cond", "sample_sparse_structure", "sample_shape_slat",
                  "sample_tex_slat", "decode_shape_slat", "decode_tex_slat", "decode_latent")
        saved = {}
        for name in stages:
            fn = getattr(self._pipe, name, None)
            if fn is None:
                continue
            saved[name] = fn

            def timed(fn=fn, name=name):
                def wrapper(*a, **k):
                    t = time.time()
                    result = fn(*a, **k)
                    print(f"[trellis] stage {name}: {time.time() - t:.1f}s", flush=True)
                    return result
                return wrapper

            setattr(self._pipe, name, timed())
        try:
            yield
        finally:
            for name, fn in saved.items():
                setattr(self._pipe, name, fn)

    def warmup(self):
        """Run one throwaway mesh before the worker registers.

        Measured on a pod: the first real generate cost 53s against 13s for every one after it —
        the encoders (DINOv3, BiRefNet) load lazily and CuMesh/triton compile their kernels on
        first use. Paying that here means no user's job is the one that pays it, and a pod that
        cannot generate at all dies at boot instead of failing a claimed job."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((96, 96, 416, 416), fill=(180, 140, 90, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        t0 = time.time()
        try:
            # Walk the exact path a real job takes so the first-use costs it pays are the ones we
            # pay here: same ptype and texture size the /generate defaults use (a first-use cost
            # keyed on texture resolution would otherwise go unwarmed). Only decimation is dropped
            # — it bounds output size, not which kernels compile.
            self._ensure()
            with self._stage_timers():
                self._generate(img, self.ptype, texture=self.texture, decimation=20000)
            self.warmup_seconds = round(time.time() - t0, 1)
            print(f"[trellis] warmed in {self.warmup_seconds:.1f}s", flush=True)
            if os.environ.get("TRELLIS_WARMUP_TIMING"):
                # A second throwaway mesh, timed per stage: its delta against the first
                # isolates each stage's one-time cost. Boot-profiling only — ~13s of pod time.
                t1 = time.time()
                with self._stage_timers():
                    self._generate(img, self.ptype, texture=self.texture, decimation=20000)
                print(f"[trellis] second warmup generate: {time.time() - t1:.1f}s", flush=True)
        except Exception as e:
            self.warmup_error = str(e)[:300]
            print(f"[trellis] warmup FAILED after {time.time() - t0:.1f}s: {self.warmup_error}",
                  flush=True)
        finally:
            # Ready either way: a warmup that failed must not strand the pod short of the
            # entrypoint's gate, where it would serve nothing and bill anyway.
            self.warmed = True

    def generate(self, png_bytes: bytes, ptype: str, texture: int, seed: int = 42) -> bytes:
        from PIL import Image
        self._ensure()
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        # decimation 50k, not 500k: the game bundles a 20k-triangle mesh (runtime/decimate.mjs),
        # so o_voxel emitting 500k was ~25x waste that also STARVED that downstream simplifier —
        # from 500k it fell back to a topology-ignoring sloppy pass (and on dense meshes stalled
        # off-budget entirely). 50k gives it a clean source: measured across character/foliage/
        # building, postprocess 4-13s -> ~2s, upload 17-22MB -> 2-3MB, final mesh equal-or-better.
        try:
            return self._generate(img, ptype, texture, decimation=50000, seed=seed)
        except Exception as e:
            if "out of memory" not in str(e).lower():
                raise
            # The voxel RESOLUTION is the memory driver (1024_cascade OOM'd CuMesh even at
            # texture 1024 on a fresh 32GB card). Degrade THAT image once — the 512 tier +
            # small texture — instead of failing it outright.
            print(f"[trellis] OOM at ptype={ptype} texture={texture}; retrying degraded",
                  flush=True)
            return self._generate(img, "512", min(texture, 512), decimation=25000, seed=seed)

    def _generate(self, img, ptype: str, texture: int, decimation: int, seed: int = 42) -> bytes:
        import gc

        import o_voxel
        import torch
        mesh = glb = None
        try:
            with torch.inference_mode():
                t0 = time.time()
                mesh = self._pipe.run(img, pipeline_type=ptype, seed=seed)[0]
                ts = time.time()
                mesh.simplify(16777216)
                t1 = time.time()
                if t1 - ts > 1:
                    print(f"[trellis] simplify {t1 - ts:.1f}s", flush=True)
                glb = o_voxel.postprocess.to_glb(
                    vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
                    coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                    aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]], decimation_target=decimation,
                    texture_size=texture, remesh=True, remesh_band=1, remesh_project=0,
                    verbose=False)
                # The diffusion sampling is already fast (~4.5s); to_glb's remesh + decimate +
                # texture bake is the rest. Log the split so the postprocess lever stays visible.
                print(f"[trellis] sample {t1 - t0:.1f}s  postprocess "
                      f"{time.time() - t1:.1f}s (decimation {decimation}, texture {texture})",
                      flush=True)
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
        return {"status": "ok", "loaded": engine.loaded, "warm": engine.warmed,
                "stage_seconds": engine.stage_seconds, "load_seconds": engine.load_seconds,
                "warmup_seconds": engine.warmup_seconds, "warmup_error": engine.warmup_error}

    @app.post("/generate")
    async def generate(request: Request):
        png = await request.body()
        if not png:
            raise HTTPException(status_code=400, detail="empty body (expected PNG bytes)")
        ptype = request.query_params.get("ptype", engine.ptype)
        if ptype not in engine.tiers:
            raise HTTPException(status_code=400,
                                detail=f"ptype {ptype} is not loaded by this process "
                                       f"(serving {sorted(engine.tiers)})")
        texture = int(request.query_params.get("texture", engine.texture))
        # The caller's seed, because a caller asking for the same image AGAIN wants a
        # different mesh — a fixed seed would hand back the one it rejected.
        seed = int(request.query_params.get("seed", 42))
        try:
            t = time.time()
            glb = engine.generate(png, ptype, texture, seed)
            gen = time.time() - t
            print(f"[trellis] ok {len(glb)} bytes {gen:.1f}s", flush=True)
            return Response(content=glb, media_type="model/gltf-binary",
                            headers={"X-Load-Seconds": str(engine.load_seconds),
                                     "X-Stage-Seconds": str(engine.stage_seconds),
                                     "X-Warmup-Seconds": str(engine.warmup_seconds),
                                     "X-Generate-Seconds": f"{gen:.1f}"})
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
    # tmpfs by default: RAM the pod already has, and the one place the network filesystem cannot
    # decide to drop our pages. Point it at a disk path (or a dir that won't fit) to opt out.
    ap.add_argument("--stage-dir", default="/dev/shm/trellis-weights",
                    help="empty string: load straight from --weights (a local disk)")
    ap.add_argument("--no-warmup", dest="warmup", action="store_false",
                    help="skip the throwaway boot mesh (the first real job then pays ~40s of "
                         "lazy encoder load + kernel compile)")
    args = ap.parse_args()

    import uvicorn
    # Warm the checkpoints while torch imports (~10s of pure CPU) rather than after it: the two
    # cost nothing together, and only the files this tier will actually load are touched.
    names = models_for({args.ptype, "512"})
    staged = {}
    warming = threading.Thread(
        target=lambda: staged.update(zip(
            ("path", "seconds"), stage_weights(args.weights, names, args.stage_dir))),
        name="stage", daemon=True)
    warming.start()

    engine = TrellisEngine(args.repo, args.weights, args.ptype, args.texture)

    # Load ahead of the first request instead of inside it: on a pod the load would otherwise
    # land in a claimed job, where it is wall-clock the user waits for AND exec_seconds debited
    # to their grant. /health answers immediately either way (it reports `loaded`).
    def preload():
        warming.join()
        engine.load_from = staged.get("path", args.weights)
        engine.stage_seconds = round(staged.get("seconds", 0.0), 1)
        engine._ensure()
        if args.warmup:
            engine.warmup()
        else:
            engine.warmed = True

    threading.Thread(target=preload, name="preload", daemon=True).start()
    print(f"[trellis] serving on {args.host}:{args.port}", flush=True)
    uvicorn.run(build_app(engine), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
