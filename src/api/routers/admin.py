"""Admin only APIs. These are gated by `require_admin`."""

import calendar
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from auth import store as auth_store
from auth.deps import require_admin
from auth.store import User
from config.settings_manager import settings_manager
from db import store as db_store
from db.estimates import QUEUE_SECONDS
from maestro.codegen import build_chain
from scaler.runpod_client import RunPodClient

logger = logging.getLogger("admin")

router = APIRouter()

_DAY_SECONDS = 86400
_NEXT_LIMIT = 10


def _max_workers(queue: str) -> int:
    """The maximum workers to spin up for a queue"""
    rp = settings_manager.get_settings().get("runpod") or {}
    if not rp.get("enabled"):
        return 0
    return int((rp.get("queues") or {}).get(queue, {}).get("max_workers", 0))


def _freshness() -> float:
    """How many seconds after a worker heartbeat before it's considered stale."""
    rp = settings_manager.get_settings().get("runpod") or {}
    return float(rp.get("stale_worker_seconds", 180))


def _stockouts(queue: str, now: float) -> Dict[str, Any]:
    """Returns stock refusal counts per window, and whether there's an active stock outage."""
    stats = db_store.pod_stockout_stats(queue, now)
    return {
        "last_1h": stats["last_1h"],
        "last_24h": stats["last_24h"],
        "last_7d": stats["last_7d"],
        "last_at": stats["last_at"],
    }


def _workers(queue: str, now: float, freshness: float) -> List[Dict[str, Any]]:
    """Returns a list of live workers."""
    held = {j["worker_id"]: j for j in db_store.claimed_jobs(queue)}
    rows = []
    for w in db_store.booting_workers(queue) + db_store.live_workers(queue, freshness):
        job = held.get(w["id"])
        rows.append({
            "id": w["id"],
            "state": "booting" if w["registered_at"] is None else "busy" if job else "idle",
            "gpu_type": w["gpu_type"],
            "usd_per_hour": w["usd_per_hour"],
            "source": w["source"],
            "pod_id": w["pod_id"],
            "uptime_seconds": now - w["started_at"],
            "last_seen_seconds": now - (w["last_seen_at"] or w["started_at"]),
            "busy_seconds": w["busy_seconds"],
            "job": job and {"id": job["id"], "game_id": job["game_id"], "build_id": job["build_id"],
                            "running_seconds": now - (job["started_at"] or now),
                            "est_seconds": QUEUE_SECONDS[queue]},
        })
    return rows


@router.get("/queues")
async def get_queues(_: User = Depends(require_admin)) -> Dict[str, Any]:
    """Returns a queue snapshot: depth and backlog, current scaled fleet."""
    now = time.time()
    freshness = _freshness()

    queues: List[Dict[str, Any]] = []
    totals = {"pending": 0, "claimed": 0, "workers_live": 0, "backlog_seconds": 0.0}

    for q in QUEUE_SECONDS:
        stats = db_store.queue_stats(q)
        workers = _workers(q, now, freshness)
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
                      "waiting_seconds": now - j["created_at"], "est_seconds": QUEUE_SECONDS[q]}
                     for j in db_store.pending_jobs_head(q, _NEXT_LIMIT)],
            "workers": workers,
            "stockouts": _stockouts(q, now),
        }
        queues.append(row)
        for k in totals:
            totals[k] += row[k]

    return {"queues": queues, "totals": totals}


@router.get("/analytics")
async def get_analytics(days: int = 14, _: User = Depends(require_admin)) -> Dict[str, Any]:
    """Returns the usage funnel: per day, event counts by kind plus distinct active users."""
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
    """Safety refusals, with the offending account's handle."""
    handles = {u.id: u.handle for u in auth_store.list_users()}
    rows = db_store.list_violations()
    for r in rows:
        r["handle"] = handles.get(r["user_id"])
    return {"violations": rows}


_COST_CACHE_TTL = 300.0
_MAX_COST_DAYS = 30
_cost_cache: Dict[int, Dict[str, Any]] = {}

Spend = Dict[str, float]


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _ledger(since: float, now: float) -> Optional[List[Dict[str, Any]]]:
    """Returns RunPod's hourly billing records since `since`, or None if unreachable."""
    key = (settings_manager.get_settings().get("runpod") or {}).get("api_key")
    if not key:
        return None
    try:
        return [{"pod_id": r["podId"],
                 "end": calendar.timegm(time.strptime(r["endTime"], "%Y-%m-%dT%H:%M:%SZ")),
                 "total": float(r.get("totalAmount") or 0),
                 "gpu": float(r.get("gpuAmount") or 0),
                 "disk": float(r.get("diskAmount") or 0)}
                for r in RunPodClient(key).billing_pods(_iso(since), _iso(now), bucket="hour")]
    except Exception:
        return None


def _attribute(records: List[Dict[str, Any]],
               lives: Dict[str, List[Tuple[float, str]]]) -> Tuple[Dict[str, Spend], Dict[str, Spend]]:
    """Returns spend per worker id, and ghost spend per pod id."""
    by_life: Dict[str, Spend] = {}
    ghosts: Dict[str, Spend] = {}
    for r in records:
        # RunPod reuses pod ids, so an hour belongs to the life that last started before it ended.
        owner = next((wid for started, wid in reversed(lives.get(r["pod_id"], []))
                      if started < r["end"]), None)
        slot = (by_life.setdefault(owner, {"total": 0.0, "gpu": 0.0, "disk": 0.0}) if owner
                else ghosts.setdefault(r["pod_id"], {"total": 0.0, "gpu": 0.0, "disk": 0.0}))
        for k in slot:
            slot[k] += r[k]
    return by_life, ghosts


def _pod_row(pod: Dict[str, Any], spend: Optional[Spend]) -> Dict[str, Any]:
    """Returns one pod life's costs row."""
    rate = pod.get("usd_per_hour")
    return {
        "worker_id": pod.get("worker_id"),
        "pod_id": pod["pod_id"],
        "tracked": pod.get("worker_id") is not None,
        "queue": pod.get("queue"),
        "gpu_type": pod.get("gpu_type"),
        "usd_per_hour": rate,
        "started_at": pod.get("started_at"),
        "terminated_at": pod.get("terminated_at"),
        "runpod_usd": round(spend["total"], 4) if spend else None,
        "disk_usd": round(spend["disk"], 4) if spend else None,
        # RunPod's v2 ledger bills money only.
        "billed_seconds": spend["gpu"] / rate * 3600.0 if spend and rate else None,
        "jobs": pod.get("jobs", 0),
        "failed": pod.get("failed", 0),
        "exec_seconds": pod.get("exec_seconds", 0.0),
        "customer_usd": round(pod.get("customer_micros", 0) / 1e6, 4),
    }


def _games(since: float) -> Dict[str, Any]:
    """Returns the count and per-game averages of games built since `since`."""
    game_ids = db_store.games_built_since(since)
    per_game = db_store.games_exec_seconds_by_gpu(game_ids).values()
    n = len(game_ids)
    seconds = sum(g["seconds"] for g in per_game)
    usd = sum(g["micros"] for g in per_game) / 1e6
    return {"n": n,
            "avg_gpu_hours": round(seconds / 3600.0 / n, 4) if n else None,
            "avg_usd": round(usd / n, 4) if n else None,
            "avg_changes": round(db_store.games_change_count(game_ids) / n, 2) if n else None}


@router.get("/costs")
async def get_costs(days: int = 7, _: User = Depends(require_admin)) -> Dict[str, Any]:
    """Returns every pod life created in the last `days`, plus ghost spend."""
    days = max(1, min(days, _MAX_COST_DAYS))
    now = time.time()
    hit = _cost_cache.get(days)
    if hit is not None and now - hit["generated_at"] < _COST_CACHE_TTL:
        return hit

    since = now - days * _DAY_SECONDS
    records = await asyncio.to_thread(_ledger, since, now)
    by_life, ghosts = _attribute(records or [], db_store.pod_lives())
    pods = [_pod_row(p, by_life.get(p["worker_id"])) for p in db_store.pod_ledger(since)]
    pods += [_pod_row({"pod_id": pid}, spend)
             for pid, spend in sorted(ghosts.items(), key=lambda kv: -kv[1]["total"])]

    data = {"generated_at": now, "cache_seconds": _COST_CACHE_TTL, "days": days,
            "runpod_reachable": records is not None, "pods": pods, "games": _games(since)}
    _cost_cache[days] = data
    return data


@router.post("/games/{run_id}/stop")
async def stop_any_game(run_id: str, admin: User = Depends(require_admin)) -> Dict[str, Any]:
    """Stop any run, no matter what."""
    owner = db_store.owner_of(run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    logger.warning("admin %s stopping run %s owned by %s", admin.id, run_id, owner)
    if not await asyncio.to_thread(build_chain.stop, run_id):
        raise HTTPException(status_code=409, detail="nothing in flight for this run")
    return {"run_id": run_id, "status": "stopped", "owner": owner}
