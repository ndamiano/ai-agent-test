"""Operator-only surfaces. Everything here is gated by `require_admin`, so a signed-in ordinary
user gets a 403: the inference-queue snapshot (depth, next jobs, fleet) the operator watches to
size the fleet, the effective-cost join against RunPod's ledger, and the usage rollup (user-action
events by kind by day — see routers/events.py for the intake).
"""

import calendar
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from auth import store as auth_store
from auth.deps import require_admin
from auth.store import User
from config.settings_manager import settings_manager
from db import store as db_store
from db.estimates import QUEUE_SECONDS, gpu_rate
from scaler.runpod_client import RunPodClient

logger = logging.getLogger("admin")

router = APIRouter()

_DAY_SECONDS = 24 * 3600
_NEXT_LIMIT = 10


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


def _usd_per_hour(gpu_type) -> Optional[float]:
    if not gpu_type:
        return None
    billing = settings_manager.get_settings()["billing"]
    return round(gpu_rate(gpu_type) * float(billing["usd_per_5090_hour"]), 2)


def _workers(request: Request, queue: str, now: float, freshness: float) -> List[Dict[str, Any]]:
    """Every worker the queue has, live ones from their rows and booting ones from the scaler's
    last pod listing: a pod RunPod is billing that no worker has registered from yet is still
    fleet, and the admin view is the one place that shows it."""
    held = {j["worker_id"]: j for j in db_store.claimed_jobs(queue)}
    rows = []
    for w in db_store.live_workers(queue, freshness):
        job = held.get(w["id"])
        rows.append({
            "id": w["id"],
            "state": "busy" if job else "idle",
            "gpu_type": w["gpu_type"],
            "usd_per_hour": _usd_per_hour(w["gpu_type"]),
            "source": w["source"],
            "pod_id": w["pod_id"],
            "uptime_seconds": now - w["started_at"],
            "last_seen_seconds": now - (w["last_seen_at"] or w["started_at"]),
            "busy_seconds": w["busy_seconds"],
            "job": job and {"id": job["id"], "game_id": job["game_id"], "build_id": job["build_id"],
                            "running_seconds": now - (job["started_at"] or now),
                            "est_seconds": job["est_seconds"]},
        })
    registered = {w["pod_id"] for w in rows}
    scaler = getattr(request.app.state, "autoscaler", None)
    for pod in (scaler.pods(queue, now) if scaler else []):
        if pod["pod_id"] in registered:
            continue
        rows.append({
            "id": pod["name"], "state": "booting", "gpu_type": None, "usd_per_hour": None,
            "source": "runpod", "pod_id": pod["pod_id"], "uptime_seconds": pod["age_seconds"],
            "last_seen_seconds": now - pod["seen_at"], "busy_seconds": 0.0, "job": None,
        })
    return rows


@router.get("/queues")
async def get_queues(request: Request, _: User = Depends(require_admin)) -> Dict[str, Any]:
    """Per-queue snapshot: depth and backlog, the next jobs in claim order, and the fleet with
    what each worker holds. Pure visualization — no spend here."""
    now = time.time()
    freshness = _freshness()

    queues: List[Dict[str, Any]] = []
    totals = {"pending": 0, "claimed": 0, "workers_live": 0, "backlog_seconds": 0.0}

    for q in QUEUE_SECONDS:
        stats = db_store.queue_stats(q)
        workers = _workers(request, q, now, freshness)
        row = {
            "queue": q,
            "pending": stats["pending"],
            "claimed": stats["claimed"],
            "oldest_pending_age_seconds": stats["oldest_pending_age_seconds"],
            "workers_live": sum(w["state"] != "booting" for w in workers),
            "workers_max": _max_workers(q),
            "est_seconds": QUEUE_SECONDS[q],
            "backlog_seconds": db_store.backlog_seconds(q),
            "next": [{"id": j["id"], "game_id": j["game_id"], "build_id": j["build_id"],
                      "waiting_seconds": now - j["created_at"], "est_seconds": j["est_seconds"]}
                     for j in db_store.pending_jobs_head(q, _NEXT_LIMIT)],
            "workers": workers,
        }
        queues.append(row)
        for k in totals:
            totals[k] += row[k]

    return {"queues": queues, "totals": totals}


@router.get("/analytics")
async def get_analytics(days: int = 14, _: User = Depends(require_admin)) -> Dict[str, Any]:
    """Usage funnel: per day, event counts by kind plus distinct active users. Newest day first."""
    days = max(1, min(days, 90))
    rollup = db_store.user_event_rollup(time.time() - days * _DAY_SECONDS)
    by_day: Dict[str, Dict[str, int]] = {}
    for r in rollup["kinds"]:
        by_day.setdefault(r["day"], {})[r["kind"]] = r["n"]
    users = {r["day"]: r["n"] for r in rollup["users"]}
    return {
        "kinds": sorted({r["kind"] for r in rollup["kinds"]}),
        "days": [{"day": d, "users": users.get(d, 0), "kinds": by_day[d]}
                 for d in sorted(by_day, reverse=True)],
    }


@router.get("/violations")
async def list_violations(_: User = Depends(require_admin)) -> Dict[str, Any]:
    """Safety refusals, newest first, with the offending account's handle — the repeat-offender
    view. Rows carry the matched term(s) only, never the flagged text."""
    handles = {u.id: u.handle for u in auth_store.list_users()}
    rows = db_store.list_violations()
    for r in rows:
        r["handle"] = handles.get(r["user_id"])
    return {"violations": rows}


# ── Costs: RunPod's ledger joined against our job/worker logs ────────────────────────────────
#
# Our jobs record EXEC time; RunPod bills pod WALL-CLOCK — cold starts, idle linger, warmup and
# boot-loop pods that never worked at all. The join is the point: the gap between the two IS the
# overhead, and a pod RunPod billed that no worker row ever claimed is GHOST spend (measured
# 2026-08-01: two ninfer boot-loops billed ~15 min each, invisible to every jobs-derived number).

_WINDOWS = [("24h", 24), ("7d", 7 * 24), ("30d", 30 * 24)]
_COST_CACHE_TTL = 300.0
_cost_cache: Dict[str, Any] = {"at": 0.0, "data": None}


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _billing_rows(now: float):
    """Three fetches: hourly for the 24h window (day buckets truncate it), daily for 7d/30d,
    and per-pod for ghost accounting. None ⇒ no key or the API refused — the panel still shows
    our half."""
    key = (settings_manager.get_settings().get("runpod") or {}).get("api_key")
    if not key:
        return None
    client = RunPodClient(key)
    try:
        return {
            "hourly_24h": client.billing_pods(_iso(now - 24 * 3600), _iso(now), bucket="hour"),
            "daily_30d": client.billing_pods(_iso(now - 30 * 24 * 3600), _iso(now), bucket="day"),
            "pods_30d": client.billing_pods(_iso(now - 30 * 24 * 3600), _iso(now),
                                            bucket="day", grouping="podId"),
        }
    except Exception:
        return None


def _parse_time(value: str) -> float:
    """RunPod's docs say date-time; the wire says '2026-08-01 19:00:00' — accept both. 0.0 (never
    inside any window) for anything else, so a format surprise shows as missing spend, not a 500."""
    try:
        return calendar.timegm(time.strptime(value[:19].replace(" ", "T"), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0


def _sum_rows(rows: List[Dict], since: float) -> Dict[str, Any]:
    total, seconds, by_gpu = 0.0, 0.0, {}
    for r in rows:
        if _parse_time(r.get("time", "")) < since:
            continue
        amount = float(r.get("amount") or 0)
        secs = float(r.get("timeBilledMs") or 0) / 1000.0
        total += amount
        seconds += secs
        gpu = r.get("gpuTypeId") or "unknown"
        slot = by_gpu.setdefault(gpu, {"gpu": gpu, "amount_usd": 0.0, "billed_seconds": 0.0})
        slot["amount_usd"] += amount
        slot["billed_seconds"] += secs
    return {"amount_usd": round(total, 4), "billed_seconds": seconds,
            "by_gpu": sorted(by_gpu.values(), key=lambda g: -g["amount_usd"])}


def _worker_wall(rows: List[Dict], since: float, now: float) -> float:
    wall = 0.0
    for w in rows:
        start = max(float(w["started_at"] or since), since)
        end = float(w["terminated_at"] or w["last_seen_at"] or now)
        wall += max(0.0, min(end, now) - start)
    return wall


def _window(label: str, hours: int, billing_rows, jobs, workers_wall, worker_count) -> Dict:
    exec_seconds = jobs["done"]["exec_seconds"] + jobs["failed"]["exec_seconds"]
    out: Dict[str, Any] = {
        "label": label,
        "runpod": billing_rows,
        "jobs": {"done": jobs["done"]["n"], "failed": jobs["failed"]["n"],
                 "exec_seconds": exec_seconds,
                 "failed_exec_seconds": jobs["failed"]["exec_seconds"]},
        "workers": {"count": worker_count, "wall_seconds": workers_wall},
    }
    derived: Dict[str, Any] = {}
    if billing_rows and billing_rows["billed_seconds"] > 0:
        hours_billed = billing_rows["billed_seconds"] / 3600.0
        derived["usd_per_gpu_hour"] = round(billing_rows["amount_usd"] / hours_billed, 4)
        derived["utilization"] = round(exec_seconds / billing_rows["billed_seconds"], 4)
        derived["overhead_seconds"] = max(0.0, billing_rows["billed_seconds"] - exec_seconds)
    out["derived"] = derived
    return out


@router.get("/costs")
async def get_costs(_: User = Depends(require_admin)) -> Dict[str, Any]:
    """Effective cost, three windows. Cached: RunPod's billing API must not ride the panel's
    5-second poll."""
    now = time.time()
    if _cost_cache["data"] is not None and now - _cost_cache["at"] < _COST_CACHE_TTL:
        return _cost_cache["data"]

    billing = _billing_rows(now)
    windows = []
    for label, hours in _WINDOWS:
        since = now - hours * 3600
        rows = None
        if billing is not None:
            source = billing["hourly_24h"] if hours <= 24 else billing["daily_30d"]
            rows = _sum_rows(source, since)
        jobs = db_store.jobs_finished_totals(since=since)
        workers = [w for w in db_store.workers_since(since) if w.get("source") == "runpod"]
        windows.append(_window(label, hours, rows, jobs,
                               _worker_wall(workers, since, now), len(workers)))

    ghost = None
    if billing is not None:
        all_workers = db_store.workers_since(0.0)
        known = {w.get("pod_id") for w in all_workers}
        # Pods billed before the first worker row existed aren't ghosts — they predate pod
        # tracking entirely. The metric only judges the era it can see.
        tracking_from = min((float(w["started_at"]) for w in all_workers), default=now)
        ghost_rows = [r for r in billing["pods_30d"]
                      if r.get("podId") not in known and _parse_time(r.get("time", "")) >= tracking_from]
        ghost = {"pods": len({r.get("podId") for r in ghost_rows}),
                 "amount_usd": round(sum(float(r.get("amount") or 0) for r in ghost_rows), 4),
                 "billed_seconds": sum(float(r.get("timeBilledMs") or 0) / 1000.0
                                       for r in ghost_rows),
                 "tracking_from": tracking_from}

    data = {"generated_at": now, "cache_seconds": _COST_CACHE_TTL,
            "runpod_reachable": billing is not None, "windows": windows, "ghost_30d": ghost}
    _cost_cache.update(at=now, data=data)
    return data
