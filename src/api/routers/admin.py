"""Operator-only surfaces. Everything here is gated by `require_admin`, so a signed-in ordinary
user gets a 403. Read-only today: the inference-queue snapshot (depth, fleet, GPU-second spend)
the operator watches to size the fleet and see what the cards are costing.
"""

import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from auth.deps import require_admin
from auth.store import User
from config.settings_manager import settings_manager
from db import store as db_store
from db.estimates import QUEUE_SECONDS

router = APIRouter()

_DAY_SECONDS = 24 * 3600


def _max_workers(queue: str) -> int:
    """The configured pod ceiling for a queue, or 0 when RunPod isn't scaling it (home box)."""
    rp = settings_manager.get_settings().get("runpod") or {}
    if not rp.get("enabled"):
        return 0
    return int((rp.get("queues") or {}).get(queue, {}).get("max_workers", 0))


def _freshness() -> float:
    """How recently a worker must have checked in to count as live — the same staleness the scaler
    reaps on, so the admin count and the scaler agree on who's alive."""
    rp = settings_manager.get_settings().get("runpod") or {}
    return float(rp.get("stale_worker_seconds", 180))


@router.get("/queues")
async def get_queues(_: User = Depends(require_admin)) -> Dict[str, Any]:
    """Per-queue snapshot: live depth + fleet, plus GPU-second spend over all-time and the last
    24h and the projected cost of the current backlog. GPU-SECONDS ONLY — no dollar conversion;
    a $/hr price map is deliberately not modelled yet."""
    since_24h = time.time() - _DAY_SECONDS
    freshness = _freshness()

    queues: List[Dict[str, Any]] = []
    totals = {"pending": 0, "claimed": 0, "workers_live": 0, "backlog_seconds": 0.0,
              "paid_all": 0.0, "billed_all": 0.0, "paid_24h": 0.0, "billed_24h": 0.0}

    for q in QUEUE_SECONDS:
        stats = db_store.queue_stats(q)
        all_time = db_store.gpu_seconds(q)
        day = db_store.gpu_seconds(q, since=since_24h)
        workers_live = len(db_store.live_workers(q, freshness))
        backlog = db_store.backlog_seconds(q)

        row = {
            "queue": q,
            "pending": stats["pending"],
            "claimed": stats["claimed"],
            "oldest_pending_age_seconds": stats["oldest_pending_age_seconds"],
            "workers_live": workers_live,
            "workers_max": _max_workers(q),
            "est_seconds": QUEUE_SECONDS[q],
            "backlog_seconds": backlog,
            "paid_all": all_time["paid"],
            "billed_all": all_time["billed"],
            "paid_24h": day["paid"],
            "billed_24h": day["billed"],
        }
        queues.append(row)

        totals["pending"] += row["pending"]
        totals["claimed"] += row["claimed"]
        totals["workers_live"] += workers_live
        totals["backlog_seconds"] += backlog
        totals["paid_all"] += all_time["paid"]
        totals["billed_all"] += all_time["billed"]
        totals["paid_24h"] += day["paid"]
        totals["billed_24h"] += day["billed"]

    return {"queues": queues, "totals": totals}
