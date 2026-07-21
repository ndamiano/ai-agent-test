"""The control-plane scaling loop: per tick, one list_pods(), then per queue gather stats →
decide → execute. Errors are logged, never fatal — a bad tick is skipped, not a crash.

Pod age comes from first-seen tracking, not RunPod timestamps: when a pod first appears in
list_pods() we stamp it, and age = now - stamp. A control-plane restart resets ages to zero,
which only delays wedged-pod reaping by one boot_deadline — safe, and no API field to trust.
"""

import logging
import threading
import time
import uuid
from typing import Callable, Dict

from scaler.policy import (MarkWorkerTerminated, PodInfo, ScalingPolicy, StartPod,
                           TerminatePod, decide)
from scaler.runpod_client import RunPodClient, RunPodError
from scaler.stats import StatsSource

logger = logging.getLogger("scaler")


class Autoscaler:
    def __init__(self, stats: StatsSource, client: RunPodClient,
                 settings_getter: Callable[[], Dict]):
        self._stats = stats
        self._client = client
        self._settings = settings_getter
        self._stop = threading.Event()
        self._thread = None
        self._last_scale_up: Dict[str, float] = {}
        self._pod_first_seen: Dict[str, float] = {}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="autoscaler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        logger.info("autoscaler started")
        while True:
            tick_seconds = (self._settings().get("runpod") or {}).get("tick_seconds", 15)
            if self._stop.wait(tick_seconds):
                return
            try:
                self.tick()
            except Exception:
                logger.exception("autoscaler tick failed")

    def tick(self) -> None:
        settings = self._settings()
        rp = settings.get("runpod") or {}
        token = (settings.get("workqueue") or {}).get("token", "")
        now = time.time()

        pods = self._client.list_pods()
        listed = {p["id"] for p in pods}
        for pod_id in listed - self._pod_first_seen.keys():
            self._pod_first_seen[pod_id] = now
        for pod_id in self._pod_first_seen.keys() - listed:
            del self._pod_first_seen[pod_id]

        for queue, qcfg in (rp.get("queues") or {}).items():
            prefix = f"maestro-{queue}-"
            qpods = [PodInfo(p["id"], p["name"], now - self._pod_first_seen[p["id"]])
                     for p in pods if (p.get("name") or "").startswith(prefix)]
            staleness = rp.get("stale_worker_seconds", 180)
            policy = ScalingPolicy(
                max_workers=qcfg.get("max_workers", 2),
                scale_up_depth_per_worker=qcfg.get("scale_up_depth_per_worker", 10),
                scale_up_max_age_seconds=qcfg.get("scale_up_max_age_seconds", 300),
                cooldown_seconds=qcfg.get("cooldown_seconds", 90),
                boot_deadline_seconds=qcfg.get("boot_deadline_seconds", 900),
            )
            actions = decide(
                queue, policy,
                self._stats.queue_stats(queue),
                self._stats.live_workers(queue, staleness),
                self._stats.stale_workers(queue, staleness),
                self._stats.terminated_workers_with_pods(queue),
                qpods,
                now - self._last_scale_up.get(queue, 0.0),
            )
            for action in actions:
                self._execute(action, queue, qcfg, rp, token, now)

    def _execute(self, action, queue: str, qcfg: Dict, rp: Dict, token: str,
                 now: float) -> None:
        try:
            if isinstance(action, StartPod):
                name = f"maestro-{queue}-{uuid.uuid4().hex[:8]}"
                env = {
                    "CP_URL": rp.get("cp_url", ""),
                    "WORKER_TOKEN": token,
                    "GPU_TYPE": (qcfg.get("gpu_type_ids") or [""])[0],
                    "IDLE_EXIT_SECONDS": str(qcfg.get("idle_exit_seconds", 10)),
                }
                self._client.create_pod(
                    name=name,
                    template_id=qcfg["template_id"],
                    gpu_type_ids=qcfg["gpu_type_ids"],
                    network_volume_id=rp.get("network_volume_id", ""),
                    env=env,
                    cloud_type=rp.get("cloud_type", "SECURE"),
                )
                self._last_scale_up[queue] = now
                logger.info("scale-up %s: created pod %s", queue, name)
            elif isinstance(action, TerminatePod):
                self._client.terminate_pod(action.pod_id)
                if action.worker_id:
                    self._stats.mark_worker_terminated(action.worker_id)
                logger.info("reaped pod %s (%s)", action.pod_id, action.reason)
            elif isinstance(action, MarkWorkerTerminated):
                self._stats.mark_worker_terminated(action.worker_id)
        except RunPodError as e:
            logger.error("action %r on queue %s failed: %s", action, queue, e)
