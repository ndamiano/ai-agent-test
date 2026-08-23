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

from PIL import Image

from db import store as db_store
from maestro.codegen.assets import (asset_path, autocrop_image, ext_for, check_render, render_kind,
                                    render_verdict, save_image, set_defect, set_refused)
from maestro.codegen.staging import stage_for_play
from maestro.state import RunState
from tools.build_events import _emit
from tools.quilting import quilt_tile
from tools.safety import SafetyViolation, log_violation

logger = logging.getLogger(__name__)


def _first_image(result: Optional[Dict]) -> Optional[Dict]:
    for img in (result or {}).get("images") or []:
        if img.get("file"):
            return img
    return None


def _admit(md: Dict, entry: Optional[Dict]) -> Optional[str]:
    """The rendered file's path, iff policy admits it into the game. A refusal deletes the blob,
    marks the manifest entry and records the violation — the file never reaches the game folder,
    so nothing downstream (staging, archive, TRELLIS) can carry it anywhere."""
    if entry is None:
        return None
    reason = render_verdict(entry)
    if reason is None:
        return entry["file"]
    logger.warning("assets %s: %s refused — %s", md["run_id"], md["asset_id"], reason)
    Path(entry["file"]).unlink(missing_ok=True)
    set_refused(RunState(md["run_id"]).run_dir, md["asset_id"], reason)
    log_violation(SafetyViolation("nsfw_render", reason), run_id=md["run_id"],
                  source="image_render")
    return None


def _mesh_from_image(md: Dict, result: Dict) -> Optional[Dict]:
    """The image a TRELLIS job turns into a GLB. The PNG rides the payload as base64 because the
    worker is remote and cannot read the control plane's blob dir."""
    src = _admit(md, _first_image(result))
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


def _record_defect(md: Dict, dst: Path) -> None:
    """What a render came back BROKEN as, on the manifest entry the gallery and the top-up read.
    Soft: a check that cannot run must not cost the game its art."""
    try:
        defect = check_render(dst, render_kind(md.get("kind") or "sprite"))
    except Exception as e:
        logger.warning("render check %s failed: %s", md["asset_id"], e)
        return
    if defect:
        logger.warning("assets %s: %s rendered broken — %s", md["run_id"], md["asset_id"], defect)
    set_defect(RunState(md["run_id"]).run_dir, md["asset_id"], defect)


def _save_sprite(md: Dict, result: Dict) -> None:
    src = _admit(md, _first_image(result))
    if src is None:
        return
    # The worker renders png; the game holds webp. Crop before the one encode — cropping a webp
    # would decode and re-encode it, paying the quality cost twice.
    dst = asset_path(md["run_id"], md["asset_id"], "webp")
    try:
        im = Image.open(src).convert("RGBA")
        save_image(autocrop_image(im), dst)
        Path(src).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("autocrop %s failed: %s", md["asset_id"], e)
        save_image(Image.open(src).convert("RGBA"), dst)
    _record_defect(md, dst)


def _save_flat(md: Dict, result: Dict) -> None:
    """A tile or a backdrop: it IS the background, so it keeps the whole frame the sampler drew.
    No matte to crop to, and autocrop on an opaque image is a no-op that only ever misfires.
    A tile additionally gets quilted seamless — the game repeats it edge to edge, and a raw
    render's borders never match. Soft, like snapshots: a failed post-op must not cost the game
    its art."""
    src = _admit(md, _first_image(result))
    if src is None:
        return
    dst = asset_path(md["run_id"], md["asset_id"], "webp")
    im = Image.open(src)
    if md.get("kind") == "tile":
        try:
            im = quilt_tile(im)
        except Exception as e:
            logger.warning("tile quilt %s failed, saving the raw render: %s", md["asset_id"], e)
    save_image(im, dst)
    Path(src).unlink(missing_ok=True)
    _record_defect(md, dst)


def _decimate(md: Dict, result: Dict) -> None:
    from maestro.worldgen.backends.meshes import _decimate
    src = (result or {}).get("glb_file")
    if not src:
        return
    dst = asset_path(md["run_id"], md["asset_id"], "glb")
    Path(dst).write_bytes(Path(src).read_bytes())
    _decimate(Path(dst), 20_000)


OPERATIONS = {"save_sprite": _save_sprite, "save_flat": _save_flat, "decimate": _decimate}

# The scene chain rides the same registries so the completion dispatch stays one branch:
# its jobs are asset jobs whose `then` carries scene names.
from maestro.codegen import scene_chain as _scene  # noqa: E402  (imports nothing from here)

CONTINUATIONS.update(_scene.CONTINUATIONS)
OPERATIONS.update(_scene.OPERATIONS)


def _finalize_assets(md: Dict, jobs: List[Dict]) -> None:
    run_id = md["run_id"]
    state = RunState(run_id)
    ids, rendered = [], []
    for j in jobs:
        aid = j["metadata"].get("asset_id")
        if not aid or aid in ids:
            continue
        ids.append(aid)
        if asset_path(run_id, aid, ext_for(j["metadata"].get("kind"))).exists():
            rendered.append(aid)

    # A batch enqueued mid-build must not stage the game out from under the build's own finalize.
    status = (db_store.game(run_id) or {}).get("status")
    if status == "built":
        stage_for_play(state.run_dir, run_id)

    ok = status in ("built", "building")
    logger.info("assets %s: rendered %d/%d asset(s)", run_id, len(rendered), len(ids))
    build_id = next((j["build_id"] for j in jobs if j["build_id"]), None)
    _emit("assets_done", run_id, build_id=build_id, ok=ok, rendered=sorted(rendered))
    if build_id:
        db_store.build_finished(build_id, "succeeded" if ok else "failed")


FINALIZERS = {"assets": _finalize_assets, **_scene.FINALIZERS}


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


def on_completion(metadata: Dict, result: Optional[Dict], batch_id: Optional[str],
                  batch_complete: bool) -> None:
    """Everything a completion owes after its transaction commits. Runs off the request thread —
    it is bounded CPU (a decimate, a bundle), never a wait."""
    run_operations(metadata, result)
    if batch_complete and batch_id:
        run_finalize(batch_id)
