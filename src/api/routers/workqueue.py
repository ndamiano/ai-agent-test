"""Worker endpoints — the pull side of the inference queue.

Worker agents (worker/agent.py) claim jobs, heartbeat their lease, and land results here.
Mounted at /worker, OUTSIDE the user-auth gate: workers are not users. Auth is the shared
workqueue token from settings, checked on every call; unset token = everything refused, so
the queue is fail-closed until explicitly configured.
"""

import asyncio
import hmac
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import store as db_store

router = APIRouter()

CLAIM_LONG_POLL_SECONDS = 25.0
_CLAIM_POLL_INTERVAL = 0.5


def _queue_settings() -> Dict:
    from config.settings_manager import settings_manager
    return settings_manager.get_settings().get("workqueue") or {}


def _require_worker(request: Request) -> Dict:
    """The workqueue settings, iff this request carries the worker token. 403 otherwise."""
    cfg = _queue_settings()
    token = cfg.get("token") or ""
    header = request.headers.get("Authorization", "")
    presented = header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""
    if not token or not presented or not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="worker token required")
    return cfg


class ClaimBody(BaseModel):
    queue: str
    worker_id: str
    gpu_type: Optional[str] = None
    source: Optional[str] = None


class HeartbeatBody(BaseModel):
    job_id: str
    worker_id: str


class CompleteBody(BaseModel):
    job_id: str
    worker_id: str
    result: Optional[Dict] = None
    error: Optional[str] = None
    exec_seconds: float = 0.0
    gpu_type: Optional[str] = None


@router.post("/claim", response_model=Dict)
async def claim(body: ClaimBody, request: Request):
    """Long-poll for the oldest pending job on the queue. Returns {"job": null} when nothing
    arrives within the window — the agent just calls again."""
    cfg = _require_worker(request)
    lease = cfg.get("lease_seconds", 120)
    db_store.worker_seen(body.worker_id, body.queue, body.gpu_type, body.source)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CLAIM_LONG_POLL_SECONDS
    while True:
        job = await asyncio.to_thread(db_store.claim_job, body.queue, body.worker_id, lease)
        if job is not None:
            return {"job": {"id": job["id"], "queue": job["queue"], "payload": job["payload"]}}
        if loop.time() >= deadline:
            return {"job": None}
        await asyncio.sleep(_CLAIM_POLL_INTERVAL)


@router.post("/heartbeat", response_model=Dict)
async def heartbeat(body: HeartbeatBody, request: Request):
    cfg = _require_worker(request)
    ok = db_store.heartbeat_job(body.job_id, body.worker_id, cfg.get("lease_seconds", 120))
    return {"ok": ok}


@router.post("/complete", response_model=Dict)
async def complete(body: CompleteBody, request: Request):
    """Land a result (or failure). ok=false means the lease lapsed and the job was requeued —
    this worker's result was dropped and it should just move on."""
    _require_worker(request)
    ok = db_store.complete_job(body.job_id, body.worker_id, body.result, body.error,
                               body.exec_seconds, body.gpu_type)
    return {"ok": ok}
