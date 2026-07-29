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
from maestro.codegen.assets import asset_path, autocrop
from maestro.codegen.staging import stage_for_play
from maestro.state import RunState
from tools.build_events import _emit

logger = logging.getLogger(__name__)


def _first_image(result: Optional[Dict]) -> Optional[str]:
    for img in (result or {}).get("images") or []:
        if img.get("file"):
            return img["file"]
    return None


def _mesh_from_image(md: Dict, result: Dict) -> Optional[Dict]:
    """The image a TRELLIS job turns into a GLB. The PNG rides the payload as base64 because the
    worker is remote and cannot read the control plane's blob dir."""
    src = _first_image(result)
    if src is None:
        return None
    data = Path(src).read_bytes()
    # keep the mesh's source render: an img2img regenerate needs an image to seed, and a GLB isn't one
    asset_path(md["run_id"], md["asset_id"], "src.png").write_bytes(data)
    b64 = base64.b64encode(data).decode("ascii")
    return {"queue": "mesh",
            "payload": {"kind": "trellis_mesh", "image_b64": b64},
            "metadata": {**md, "then": {"operations": ["decimate"],
                                        "finalize": md["then"]["finalize"]}}}


CONTINUATIONS = {"mesh_from_image": _mesh_from_image}


def _save_sprite(md: Dict, result: Dict) -> None:
    src = _first_image(result)
    if src is None:
        return
    dst = asset_path(md["run_id"], md["asset_id"], "png")
    Path(src).replace(dst)
    try:
        autocrop(dst)
    except Exception as e:
        logger.warning("autocrop %s failed: %s", md["asset_id"], e)


def _decimate(md: Dict, result: Dict) -> None:
    from tools.comfyui_tools import _decimate_glb
    src = (result or {}).get("glb_file")
    if not src:
        return
    dst = asset_path(md["run_id"], md["asset_id"], "glb")
    Path(dst).write_bytes(Path(src).read_bytes())
    _decimate_glb(str(dst))


OPERATIONS = {"save_sprite": _save_sprite, "decimate": _decimate}


def _finalize_assets(md: Dict, jobs: List[Dict]) -> None:
    run_id = md["run_id"]
    state = RunState(run_id)
    ids, rendered = [], []
    for j in jobs:
        aid = j["metadata"].get("asset_id")
        if not aid or aid in ids:
            continue
        ids.append(aid)
        ext = "glb" if j["metadata"].get("kind") == "mesh" else "png"
        if asset_path(run_id, aid, ext).exists():
            rendered.append(aid)

    # A batch enqueued mid-build must not stage the game out from under the build's own finalize.
    status = (db_store.game(run_id) or {}).get("status")
    if status == "built":
        stage_for_play(state.run_dir, run_id)

    ok = status in ("built", "building")
    logger.info("assets %s: rendered %d/%d asset(s)", run_id, len(rendered), len(ids))
    _emit("assets_done", run_id, ok=ok, rendered=sorted(rendered))
    build_id = next((j["build_id"] for j in jobs if j["build_id"]), None)
    if build_id:
        db_store.build_finished(build_id, "succeeded" if ok else "failed")


FINALIZERS = {"assets": _finalize_assets}


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
