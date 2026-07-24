"""The asset chain: what a finished asset job does next.

A job's `metadata.then` names three things by key — the follow-up job to enqueue, the operations
to run on this result, and the batch's finalize. This module owns those names; the queue itself
stays a generic transport that never learns what an asset is.

Nothing waits. The image stage enqueues every job at once (so queue depth is real and the scaler
can see it), each image job carries the mesh job that follows it, and the completion that empties
the batch runs the finalize.
"""

import base64
import logging
from pathlib import Path
from typing import Dict, List, Optional

from db import store as db_store
from maestro.codegen.gates import build_bundle, game_dir, stage_for_play
from maestro.state import RunState
from tools.build_events import _emit

logger = logging.getLogger(__name__)


def _asset_path(run_id: str, asset_id: str, ext: str) -> Path:
    assets = game_dir(RunState(run_id).run_dir) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    return assets / f"{asset_id}.{ext}"


def _first_image(result: Optional[Dict]) -> Optional[str]:
    for img in (result or {}).get("images") or []:
        if img.get("file"):
            return img["file"]
    return None


# ── continuations: parent result → the job that follows ───────────────────────
def _mesh_from_image(md: Dict, result: Dict) -> Optional[Dict]:
    """The image a TRELLIS job turns into a GLB. The PNG rides the payload as base64 because the
    worker is remote and cannot read the control plane's blob dir."""
    src = _first_image(result)
    if src is None:
        return None
    b64 = base64.b64encode(Path(src).read_bytes()).decode("ascii")
    return {"queue": "mesh",
            "payload": {"kind": "trellis_mesh", "image_b64": b64},
            "metadata": {**md, "then": {"operations": ["decimate"],
                                        "finalize": md["then"]["finalize"]}}}


CONTINUATIONS = {"mesh_from_image": _mesh_from_image}


# ── operations: what to do with THIS job's result ─────────────────────────────
def _save_sprite(md: Dict, result: Dict) -> None:
    from maestro.codegen.reskin import _autocrop
    src = _first_image(result)
    if src is None:
        return
    dst = _asset_path(md["run_id"], md["asset_id"], "png")
    Path(src).replace(dst)
    try:
        _autocrop(dst)
    except Exception as e:
        logger.warning("autocrop %s failed: %s", md["asset_id"], e)


def _decimate(md: Dict, result: Dict) -> None:
    from tools.comfyui_tools import _decimate_glb
    src = (result or {}).get("glb_file")
    if not src:
        return
    dst = _asset_path(md["run_id"], md["asset_id"], "glb")
    Path(dst).write_bytes(Path(src).read_bytes())
    _decimate_glb(str(dst))


OPERATIONS = {"save_sprite": _save_sprite, "decimate": _decimate}


# ── finalize: the batch is done ───────────────────────────────────────────────
def _rendered(run_id: str, ids: List[str], ext: str) -> List[str]:
    return sorted(i for i in ids if _asset_path(run_id, i, ext).exists())


def _finalize_skin(md: Dict, jobs: List[Dict]) -> None:
    from maestro.codegen.reskin import fit_building_boxes
    run_id, mode = md["run_id"], md["mode"]
    state = RunState(run_id)
    ext = "glb" if mode == "3d" else "png"
    ids = sorted({j["metadata"].get("asset_id") for j in jobs
                  if j["metadata"].get("asset_id")})
    rendered = _rendered(run_id, ids, ext)

    # gate_ok is the ENQUEUE-time verdict; an early batch enqueues mid-build (gate_ok False), so
    # the game's CURRENT status decides staging. Mid-build, staging — and the 3D box-fit, which
    # rewrites world.ts under the build's feet — defer to the build's own finalize.
    status = (db_store.game(run_id) or {}).get("status")
    stage = bool(md.get("gate_ok")) or status == "built"
    if mode == "3d" and stage:
        fitted = fit_building_boxes(state.run_dir)
        if fitted:
            logger.info("assets %s: fitted %d building box(es) to their meshes", run_id, fitted)
            build_bundle(state.run_dir)
    if stage:
        stage_for_play(state.run_dir, run_id)

    ok = stage or status == "building"   # renders landed mid-build: the build stages them later
    logger.info("assets %s: rendered %d/%d asset(s)", run_id, len(rendered), len(ids))
    _emit("assets_done", run_id, ok=ok, mode=mode, rendered=rendered)
    build_id = next((j["build_id"] for j in jobs if j["build_id"]), None)
    if build_id:
        db_store.build_finished(build_id, "succeeded" if ok else "failed")


FINALIZERS = {"skin": _finalize_skin}


# ── the completion hook ───────────────────────────────────────────────────────
def build_continuation(metadata: Dict, result: Optional[Dict]) -> Optional[Dict]:
    """The follow-up job this completion should enqueue, built before the completion transaction
    so the insert stays inside it."""
    name = (metadata.get("then") or {}).get("enqueue")
    if not name or result is None:
        return None
    return CONTINUATIONS[name](metadata, result)


def run_operations(metadata: Dict, result: Optional[Dict]) -> None:
    for name in (metadata.get("then") or {}).get("operations") or []:
        try:
            OPERATIONS[name](metadata, result)
        except Exception:
            logger.exception("asset operation %s failed for %s", name, metadata.get("asset_id"))


def run_finalize(batch_id: str) -> bool:
    """Run a batch's finalize, once. The claim is atomic, so the live completion and the reaper
    can both call this and only one executes."""
    jobs = db_store.batch_jobs(batch_id)
    md = next((j["metadata"] for j in jobs if j["metadata"].get("then", {}).get("finalize")), None)
    if md is None:
        return False
    if not db_store.claim_batch_finalize(batch_id):
        return False
    try:
        FINALIZERS[md["then"]["finalize"]](md, jobs)
    except Exception:
        logger.exception("finalize of batch %s failed", batch_id)
    return True


def finalize_now(metadata: Dict, build_id: Optional[str] = None) -> None:
    """Finalize a stage that enqueued no jobs at all — an empty plan, every prompt blocked, or a
    budget refusal. There is no batch, so no completion will ever run this."""
    FINALIZERS[metadata["then"]["finalize"]](metadata, [{"metadata": {}, "build_id": build_id}])


def on_completion(metadata: Dict, result: Optional[Dict], batch_id: Optional[str],
                  batch_complete: bool) -> None:
    """Everything a completion owes after its transaction commits. Runs off the request thread —
    it is bounded CPU (a decimate, a bundle), never a wait."""
    run_operations(metadata, result)
    if batch_complete and batch_id:
        run_finalize(batch_id)
