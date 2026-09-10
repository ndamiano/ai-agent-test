"""Standalone sprite-sheet HTTP server — run BY the sprite venv (kimodo + playwright), a
long-lived process on the SAME pod as the TRELLIS server: a sheet is always rendered from a mesh
that pod has just made, so the glb never leaves the box and no second pod boots for it.

A sheet is RENDERED rather than drawn because a video model conditioned on the still as both first
AND last frame answers a one-shot — an attack, a death, a dodge — by not moving. Here the mesh is
rigged from a canonical humanoid measured onto its T-pose, a motion model animates the skeleton
from the verb the build named, and four camera angles are shot per animation. Frame count, loop
points and facings are ours.

Launch (from the sprite venv):
  <sprite_python> sprite_server.py --host 127.0.0.1 --port 8190 [--frames 8]
                                   --ref-bvh <the skeleton's standard T-pose clip>

API:
  GET  /health   -> {"status": "ok", "loaded": <bool>, "verbs": [...]}
  POST /sheet    body = {"glb_b64", "anims": [{"name","action"}], "facings", "frames"?}
                 -> 200 {"sheet_b64", "manifest", "verbs": {...}}
                    200 {"fallback": "not a humanoid", ...} when no skeleton fits the silhouette,
                        so the caller draws the sheet instead of shipping a mislabelled rig
"""
# ruff: noqa: PLC0415 — torch/kimodo/playwright belong to this venv, not to maestro's, and the
# model is loaded once at boot rather than at import.
import argparse
import asyncio
import base64
import http.server
import json
import logging
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from maestro.sprites3d import rig as rig_mod  # noqa: E402
from maestro.sprites3d import retarget as retarget_mod  # noqa: E402
from maestro.sprites3d import sheet as sheet_mod  # noqa: E402
from maestro.sprites3d import verbs as verbs_mod  # noqa: E402

logger = logging.getLogger("sprite_server")
STATE = {"model": None, "table": None, "frames": 8, "ref_bvh": None, "port": 8190}


def _motion(verb: str, out_base: str) -> str:
    """One clip of the named verb, as a BVH. The prompt is a lookup: the text encoder Kimodo was
    trained with is a gated 15 GB Llama, and its answer for a fixed library never changes, so the
    embeddings are baked and prod loads no language model at all."""
    import torch
    from kimodo.exports.bvh import save_motion_bvh
    from kimodo.skeleton import SOMASkeleton30, global_rots_to_local_rots

    model, table = STATE["model"], STATE["table"]
    prompt = table.prompt_for(verb)
    torch.manual_seed(7)
    out = model([prompt], [int(2.0 * model.fps)], num_denoising_steps=100, num_samples=1,
                multi_prompt=True, num_transition_frames=5, post_processing=True,
                return_numpy=True)
    skel = model.skeleton
    if isinstance(skel, SOMASkeleton30):
        skel = skel.somaskel77.to(out["posed_joints"].device if hasattr(
            out["posed_joints"], "device") else "cuda:0")
    joints_pos = torch.from_numpy(out["posed_joints"][0]).to("cuda:0")
    joints_rot = torch.from_numpy(out["global_rot_mats"][0]).to("cuda:0")
    save_motion_bvh(out_base + ".bvh", global_rots_to_local_rots(joints_rot, skel),
                    joints_pos[:, skel.root_idx, :], skeleton=skel, fps=model.fps,
                    standard_tpose=True)
    return out_base + ".bvh"


async def _render(work: Path, animated: dict, frames: int):
    """Every animation of one character, from four camera angles."""
    from playwright.async_api import async_playwright

    pkg = SRC / "maestro" / "sprites3d"
    for asset in ("viewer.html", "three.module.js", "GLTFLoader.js", "BufferGeometryUtils.js"):
        shutil.copy(pkg / asset, work / asset)
    handler = http.server.SimpleHTTPRequestHandler

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = Server(("127.0.0.1", 0), lambda *a, **k: handler(*a, directory=str(work), **k))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    clips = {}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                    "--enable-unsafe-swiftshader"])
            for name, glb in animated.items():
                t0, t1 = sheet_mod.active_window(glb)
                shutil.copy(glb, work / "anim.glb")
                page = await browser.new_page(viewport={"width": 300, "height": 300})
                for facing, ims in (await sheet_mod.render(
                        page, f"http://127.0.0.1:{port}/viewer.html", frames, t0, t1)).items():
                    clips[(name, facing)] = ims
                await page.close()
            await browser.close()
    finally:
        httpd.shutdown()
    return clips


def build_sheet(payload: dict) -> dict:
    table = STATE["table"]
    anims = payload["anims"]
    frames = int(payload.get("frames") or STATE["frames"])
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        mesh_glb = work / "mesh.glb"
        mesh_glb.write_bytes(base64.b64decode(payload["glb_b64"]))
        rigged = work / "rigged.glb"
        try:
            measured = rig_mod.rig_mesh(str(mesh_glb), str(rigged))
        except ValueError as e:
            if "not a humanoid" in str(e):
                return {"fallback": "not a humanoid"}
            raise

        animated, resolved, loops = {}, {}, {}
        for entry in anims:
            verb = table.resolve(entry["action"]) or table.resolve(entry["name"]) or "idle"
            resolved[entry["name"]] = verb
            clip = _motion(verb, str(work / f"m_{entry['name']}"))
            out = work / f"a_{entry['name']}.glb"
            info = retarget_mod.retarget(str(rigged), clip, str(out), ref_bvh=STATE["ref_bvh"])
            loops[entry["name"]] = bool(info.get("loops"))
            animated[entry["name"]] = str(out)

        clips = asyncio.run(_render(work, animated, frames))
        png, manifest = sheet_mod.pack(clips, [a["name"] for a in anims], loops=loops)
        buf = work / "sheet.png"
        png.save(buf)
        return {"sheet_b64": base64.b64encode(buf.read_bytes()).decode("ascii"),
                "manifest": manifest, "verbs": resolved, "measured": measured}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def _send(self, code, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {"status": "ok", "loaded": STATE["model"] is not None,
                             "verbs": sorted(set(STATE["table"].names)) if STATE["table"] else []})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/sheet"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        started = time.time()
        try:
            out = build_sheet(payload)
        except Exception as e:            # noqa: BLE001 — the worker turns any failure into a job error
            logger.exception("sheet failed")
            self._send(500, {"error": str(e)[:2000]})
            return
        out["generate_seconds"] = round(time.time() - started, 1)
        self._send(200, out)


def _packaged_tpose() -> str:
    """The skeleton's standard T-pose clip ships inside kimodo, so it is found rather than
    provisioned."""
    import kimodo
    found = Path(kimodo.__file__).parent / "assets/skeletons/somaskel77/somaskel77_standard_tpose.bvh"
    return str(found) if found.exists() else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--ref-bvh", default=os.environ.get("KIMODO_TPOSE_BVH") or _packaged_tpose())
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    STATE.update(frames=args.frames, ref_bvh=args.ref_bvh, port=args.port)
    if not STATE["ref_bvh"]:
        raise SystemExit("--ref-bvh (the skeleton's standard T-pose clip) is required")

    from kimodo import load_model
    STATE["table"] = verbs_mod.Table()
    t0 = time.time()
    STATE["model"] = load_model(None, device="cuda:0", default_family="Kimodo",
                                text_encoder=STATE["table"])
    logger.info("kimodo loaded in %.1fs, %d verbs, no text encoder",
                time.time() - t0, len(set(STATE["table"].names)))

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server((args.host, args.port), Handler) as httpd:
        logger.info("sprite server on %s:%d", args.host, args.port)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
