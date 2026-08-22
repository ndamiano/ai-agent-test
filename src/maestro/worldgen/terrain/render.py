"""Rendering T in three.js.

The refinement loop needs to look at the world, and until now the only view of
it was a numpy hillshade — which shows geometry honestly and says nothing about
whether the materials are the right scale, whether the scattered rocks are the
right size, or whether any of it holds together as a place.

This writes `world.json` beside the world's files and draws it in a headless
browser through `runtime/vendor/world.js` — the SAME loader the generated game
imports. A view rendered by anything else is a view of a world nobody plays: the
one thing this stage must never do is show the refinement agent a picture the
game cannot reproduce.

Every path in the job is relative to the folder the job sits in, so a world that
is copied or moved renders its own files rather than the original's, and the
folder can be served to a browser as-is.

The height field goes across as raw float32 rather than an image: a PNG would
quantise metres into 256 steps, which is visible as terracing on any gentle
slope, and the mesh builder wants a flat buffer anyway.
"""
from __future__ import annotations

import base64
import functools
import http.server
import json
import os
import socketserver
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .bake import pack_masks
from .models import TerrainPlan

VENDOR = Path(__file__).resolve().parents[4] / "runtime" / "vendor"
PAGE = "world_render.html"
JOB_NAME = "world.json"

# WebGL in a headless chromium with no GPU: SwiftShader is the software path,
# and without the unsafe flag recent chromiums answer the context request with
# nothing at all rather than falling back.
CHROMIUM_ARGS = [
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--use-gl=angle",
]


def camera_ring(
    plan: TerrainPlan,
    height: np.ndarray,
    *,
    count: int = 4,
    elevation_deg: float = 22.0,
) -> list[dict]:
    """Views around the world, plus one from directly above.

    The oblique views are what show whether a place reads as a place; the
    top-down one is what shows whether the regions ended up where the layout map
    put them. The refinement loop wants both, for different questions.
    """
    size = plan.world.size_m
    centre = [size * 0.5, float(np.median(height)), size * 0.5]
    top = float(height.max())
    shots = [{
        "name": "view_top.png",
        "position": [size * 0.5, top + size * 1.15, size * 0.5],
        "look_at": [size * 0.5, top, size * 0.5],
        "fov": 50.0,
    }]
    radius = size * 0.62
    for index in range(count):
        angle = 2.0 * np.pi * index / count
        shots.append({
            "name": f"view_{index}.png",
            "position": [
                size * 0.5 + radius * float(np.cos(angle)),
                top + size * np.tan(np.deg2rad(elevation_deg)) * 0.6,
                size * 0.5 + radius * float(np.sin(angle)),
            ],
            "look_at": centre,
            "fov": 55.0,
        })
    return shots


def write_job(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    cameras: list[dict] | None = None,
    mesh_resolution: int = 512,
) -> Path:
    """Write `world.json` and the buffers it names, and return its path.

    Paths are written relative to `out_dir`, which is what makes the folder both
    a world a browser can be pointed at and a world that survives being copied.
    """
    out_dir = Path(out_dir)
    height = np.load(out_dir / "heightmap.npy").astype(np.float32)
    (out_dir / "heightmap.f32").write_bytes(height.tobytes())

    weight_textures = pack_masks(plan, out_dir)
    # Re-rooted at out_dir rather than trusted as written. The manifest records
    # the paths that existed when the textures were generated, so a run that was
    # copied or moved — which is exactly what refining a variant of a world looks
    # like — reads the ORIGINAL's textures and renders them, silently, while
    # reporting that it used the new ones.
    materials = {}
    for row in json.loads((out_dir / "materials" / "materials.json").read_text()):
        row = dict(row)
        for channel in ("albedo", "normal", "roughness",
                        "variant_albedo", "variant_normal", "variant_roughness"):
            if row.get(channel):
                row[channel] = f"materials/{Path(row[channel]).name}"
        materials[row["region_id"]] = row
    prototypes = {}
    for row in json.loads((out_dir / "prototypes.json").read_text()):
        row = dict(row)
        if row.get("mesh"):
            row["mesh"] = f"subjects/meshes/{Path(row['mesh']).name}"
        prototypes[row["category"]] = row
    scatter = json.loads((out_dir / "scatter.json").read_text())

    grouped: dict[str, dict] = {}
    for item in scatter["instances"]:
        prototype = prototypes.get(item["category"], {})
        if not prototype.get("mesh"):
            continue  # a category whose reconstruction failed simply does not appear
        grouped.setdefault(item["category"], {
            "mesh": prototype["mesh"],
            "placements": [],
        })["placements"].append({
            "position": item["position"],
            "yaw_deg": item["yaw_deg"],
            "scale": item["scale"],
            "height_m": item["height_m"],
        })

    # objects placed by 2.3, if this world has been through that stage. Each is
    # its own mesh at its own transform, so each is its own group of one rather
    # than an instanced category.
    objects_path = out_dir / "objects.json"
    if objects_path.exists():
        for item in json.loads(objects_path.read_text()):
            mesh = item.get("mesh")
            if not mesh:
                continue
            # re-rooted the way the material paths are, and for the same reason:
            # the manifest records where the GLB was written, so a copied world
            # renders the ORIGINAL's objects while reporting it used its own
            relative = f"objects/{Path(mesh).parent.name}/{Path(mesh).name}"
            if not (out_dir / relative).exists():
                continue
            # keyed by region as well as index: the index restarts at zero in
            # every region, so keying on it alone means the last region silently
            # overwrites every object the earlier ones placed
            key = f"object-{item.get('region_id', 'r')}-{item['index']}"
            grouped[key] = {
                "mesh": relative,
                "placements": [{
                    "position": item["position"],
                    "yaw_deg": item["yaw_deg"],
                    "scale": 1.0,
                    "height_m": item["height_m"],
                }],
            }

    job = {
        "heightmap": "heightmap.f32",
        "resolution": int(height.shape[0]),
        "mesh_resolution": mesh_resolution,
        "size_m": plan.world.size_m,
        "sea_level_m": plan.world.sea_level_m,
        "lowest_m": float(height.min()),
        "sun_elevation_deg": 42.0,
        "sun_azimuth_deg": 135.0,
        "weight_textures": [_relative(p, out_dir) for p in weight_textures],
        "regions": [
            {
                "region_id": row.region_id,
                "category": row.category,
                # in metres, so a game can ask what region it is standing in
                # without knowing the layout's unit square
                "centre_m": [
                    row.center[0] * plan.world.size_m, row.center[1] * plan.world.size_m,
                ],
                "radius_m": row.radius * plan.world.size_m,
                # texture paths come from the manifest, because only the stage
                # that generated them knows where they went
                "albedo": materials[row.region_id]["albedo"],
                "normal": materials[row.region_id]["normal"],
                # the worn second surface, when the materials stage drew one. A
                # world made before the pair existed, or one whose variant render
                # was refused, names neither and the loader draws the base alone.
                **{
                    key: materials[row.region_id][key]
                    for key in ("variant_albedo", "variant_normal")
                    if materials[row.region_id].get(key)
                },
                # ...but the scale comes from the plan, which is the thing
                # refinement edits. Reading it from the manifest instead makes
                # every scale correction a no-op that still reports success.
                "scale_m": plan.material_for(row.region_id).scale_m,
            }
            for row in plan.layout
        ],
        "instances": grouped,
        "cameras": cameras if cameras is not None else camera_ring(plan, height),
    }
    path = out_dir / JOB_NAME
    path.write_text(json.dumps(job, indent=1))
    return path


def _relative(path: Path | str, out_dir: Path) -> str:
    """A path as the world names it: relative to the folder the job sits in."""
    return os.path.relpath(Path(path).resolve(), out_dir.resolve())


@contextmanager
def _serve(out_dir: Path):
    """Serve the world, with the vendored renderer behind it on the same origin.

    Two roots rather than a copy of the js beside every world: the game gets its
    own copy from `seed_vendor`, and a world folder is data. One origin because
    a module import and a fetch of the height buffer both have to reach it.
    """
    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            local = super().translate_path(path)
            if os.path.exists(local):
                return local
            return str(VENDOR / os.path.basename(local))

        def log_message(self, *args):
            pass

    httpd = socketserver.TCPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(out_dir)),
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _shoot(out_dir: Path, call: str, argument, resolution: str, timeout: float) -> list[Path]:
    """Open the render page over `out_dir` and write what it hands back.

    The page answers with a data URL per shot rather than writing files itself:
    a headless browser has no way to reach the disk, and a canvas readback is
    the only picture that exists.
    """
    from playwright.sync_api import sync_playwright

    width, _, height = resolution.partition("x")
    written: list[Path] = []
    with _serve(out_dir) as base_url:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": int(width), "height": int(height)})
            failures: list[str] = []
            page.on("pageerror", lambda e: failures.append(str(e)))
            page.goto(f"{base_url}/{PAGE}", timeout=int(timeout * 1000), wait_until="load")
            page.wait_for_function("window.rendererReady === true", timeout=int(timeout * 1000))
            shots = page.evaluate(
                f"([arg, w, h]) => window.{call}(arg, w, h)",
                [argument, int(width), int(height)],
            )
            browser.close()
    if failures:
        raise RuntimeError("render page threw: " + "; ".join(failures[:3]))
    for shot in shots:
        path = out_dir / shot["name"]
        path.write_bytes(base64.b64decode(shot["png"].split(",", 1)[1]))
        written.append(path)
    return written


def render(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    cameras: list[dict] | None = None,
    resolution: str = "1280x800",
    mesh_resolution: int = 512,
    timeout: float = 600.0,
) -> list[Path]:
    """Render the world and return the images written, in camera order."""
    out_dir = Path(out_dir).resolve()
    job_path = write_job(plan, out_dir, cameras=cameras, mesh_resolution=mesh_resolution)
    job = json.loads(job_path.read_text())
    for shot in job["cameras"]:
        (out_dir / shot["name"]).unlink(missing_ok=True)

    written = _shoot(out_dir, "renderWorld", f"./{JOB_NAME}", resolution, timeout)
    names = [shot["name"] for shot in job["cameras"]]
    if [p.name for p in written] != names:
        raise RuntimeError(
            f"rendered {len(written)} of {len(names)} views: {[p.name for p in written]}"
        )
    return [out_dir / name for name in names]


def render_meshes(
    items: list[dict],
    out_dir: Path | str,
    *,
    resolution: str = "512x512",
    timeout: float = 600.0,
) -> list[Path]:
    """Photograph each GLB on its own, so a reconstruction can be looked at.

    A flatness number says a mesh is a card; only a picture says what kind of
    card. Each item is {"mesh": path, "name": png name}, both relative to
    `out_dir`; the camera frames each mesh from its own bounding box, so a
    barrel and a fence both fill the frame.
    """
    out_dir = Path(out_dir).resolve()
    return _shoot(out_dir, "renderMeshes", items, resolution, timeout)


__all__ = ["render", "render_meshes", "write_job", "camera_ring", "JOB_NAME"]
