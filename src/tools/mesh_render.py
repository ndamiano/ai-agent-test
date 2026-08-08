"""One orthographic render of a GLB: the store's sprite leg. The camera is a real camera
shared by every object, so all sprites agree on projection and lighting by construction —
that agreement is the whole reason objects render through 3D at all.

Playwright chromium against the vendored three.js; ANGLE gl-egl because headless default GL
loses context mid-render. CAMERA names the setup in store meta — change the page, change
the name, and stale sprites re-render on their next lookup miss.
"""

import http.server
import shutil
import socket
import tempfile
import threading
from pathlib import Path

CAMERA = "ortho45-768"

_VENDOR = Path(__file__).resolve().parents[2] / "runtime" / "vendor"

_PAGE = """<!doctype html><body style="margin:0;background:transparent">
<script type="module">
import * as THREE from './three.module.js';
import { GLTFLoader } from './GLTFLoader.js';
const W = 768, H = 768;
const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true,
                                          preserveDrawingBuffer: true});
renderer.setSize(W, H);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const elev = Math.PI / 4;
const d = 3;
const cam = new THREE.OrthographicCamera(-0.75, 0.75, 0.75, -0.75, 0.01, 20);
cam.position.set(0, Math.sin(elev) * d, Math.cos(elev) * d);
cam.lookAt(0, 0, 0);
scene.add(new THREE.AmbientLight(0xffffff, 1.1));
const sun = new THREE.DirectionalLight(0xfff4e0, 1.6);
sun.position.set(2, 4, 3);
scene.add(sun);
new GLTFLoader().load('./model.glb', (g) => {
  const obj = g.scene;
  const box = new THREE.Box3().setFromObject(obj);
  const c = box.getCenter(new THREE.Vector3());
  obj.position.sub(c);
  scene.add(obj);
  renderer.render(scene, cam);
  window.__done = true;
});
</script></body>"""


def render_glb(glb: bytes, out_png: Path) -> None:
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        (tdir / "model.glb").write_bytes(glb)
        (tdir / "index.html").write_text(_PAGE)
        for f in ("three.module.js", "GLTFLoader.js", "BufferGeometryUtils.js"):
            shutil.copy(_VENDOR / f, tdir / f)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(
            *a, directory=str(tdir), **k)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=gl-egl"])
                try:
                    page = b.new_page(viewport={"width": 768, "height": 768})
                    page.goto(f"http://127.0.0.1:{port}/index.html")
                    page.wait_for_function("window.__done === true", timeout=30000)
                    page.locator("canvas").screenshot(path=str(out_png), omit_background=True)
                finally:
                    b.close()
        finally:
            srv.shutdown()
