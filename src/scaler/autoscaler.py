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

from scaler.policy import (
    MarkWorkerTerminated,
    PodInfo,
    ScalingPolicy,
    StartPod,
    TerminatePod,
    decide,
)
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
                self._execute(action, queue, qcfg, rp, token,
                              (settings.get("llm") or {}).get("model", ""), now)

    def _execute(self, action, queue: str, qcfg: Dict, rp: Dict, token: str, llm_model: str,
                 now: float) -> None:
        try:
            if isinstance(action, StartPod):
                name = f"maestro-{queue}-{uuid.uuid4().hex[:8]}"
                env = {
                    "CP_URL": rp.get("cp_url", ""),
                    "WORKER_TOKEN": token,
                    "IDLE_EXIT_SECONDS": str(qcfg.get("idle_exit_seconds", 10)),
                }
                if queue == "llm":
                    # An llm pod picks its engine by the card it got, and ninfer answers only
                    # requests naming its --model-id. The pod cannot read settings, so the model
                    # string every request will carry is delivered at create.
                    env["LLM_MODEL"] = llm_model
                self._start_pod(name, queue, qcfg, rp, env)
                self._last_scale_up[queue] = now
            elif isinstance(action, TerminatePod):
                self._client.terminate_pod(action.pod_id)
                if action.worker_id:
                    self._stats.mark_worker_terminated(action.worker_id)
                logger.info("reaped pod %s (%s)", action.pod_id, action.reason)
            elif isinstance(action, MarkWorkerTerminated):
                self._stats.mark_worker_terminated(action.worker_id)
        except RunPodError as e:
            logger.error("action %r on queue %s failed: %s", action, queue, e)

    def _start_pod(self, name: str, queue: str, qcfg: Dict, rp: Dict, env: Dict) -> None:
        """Create one pod, PREFERRING the first gpu_type_ids entry.

        The list is a preference set RunPod satisfies by availability, and it documents no
        priority order — asking for all of them at once is asking for whichever is cheapest to
        hand out. So ask for the head alone first and widen only when that create is refused: the
        cards differ in what they can serve (ninfer needs a 5090), which makes the fallback a real
        downgrade rather than a substitution."""
        ids = list(qcfg["gpu_type_ids"])
        attempts = [ids[:1], ids] if len(ids) > 1 else [ids]
        for i, attempt in enumerate(attempts):
            try:
                self._client.create_pod(
                    name=name,
                    template_id=qcfg["template_id"],
                    gpu_type_ids=attempt,
                    network_volume_id=rp.get("network_volume_id", ""),
                    env=env,
                    cloud_type=rp.get("cloud_type", "SECURE"),
                )
            except RunPodError as e:
                if i == len(attempts) - 1:
                    raise
                logger.warning("scale-up %s: create with %s refused (%s) — widening to %s",
                               queue, ids[0], e, ids)
                continue
            logger.info("scale-up %s: created pod %s on %s", queue, name, attempt)
            return
