"""The control-plane scaling loop: per tick, one list_pods(), then per queue gather stats →
decide → execute. Errors are logged, never fatal — a bad tick is skipped, not a crash.

A pod's age is its worker row's started_at — the create — and survives a control-plane restart.
A listed pod with our prefix and no row (created before the row existed, or by hand) is aged
from the tick that first saw it; a restart resets that to zero, which only delays reaping such
a pod by one boot_deadline.
"""

import logging
import threading
import time
import uuid
from typing import Callable, Dict, List

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

# What RunPod's create answers when the ask is fine and the stock is not — the two texts seen on
# prod 2026-09-04 for a card and a datacenter respectively. Any other refusal (auth, template,
# network) is our problem, not the provider's inventory, and is recorded under its own kind.
_STOCK_MARKERS = ("no instances currently available",
                  "could not find any pods with required specifications")


def refusal_kind(errors: List[str]) -> str:
    return "stock" if all(any(m in e.lower() for m in _STOCK_MARKERS) for e in errors) else "other"


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

        # A create that did not state the price leaves the row unpriced; the listing states it.
        rate_by_pod = {p["id"]: p["costPerHr"] for p in pods if p.get("costPerHr") is not None}
        for w in self._stats.unpriced_pod_workers():
            if w.pod_id in rate_by_pod:
                self._stats.record_worker_rate(w.id, float(rate_by_pod[w.pod_id]))

        for queue, qcfg in (rp.get("queues") or {}).items():
            prefix = f"maestro-{queue}-"
            created = {b.pod_id: b.started_at for b in self._stats.booting_workers(queue)}
            qpods = [PodInfo(p["id"], p["name"],
                             now - created.get(p["id"], self._pod_first_seen[p["id"]]))
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
                self._stats.stale_workers(queue, staleness, policy.boot_deadline_seconds),
                self._stats.terminated_workers_with_pods(queue),
                qpods,
                now - self._last_scale_up.get(queue, 0.0),
            )
            for action in actions:
                self._execute(action, queue, qcfg, rp, token, settings["llm"], now)

    def _execute(self, action, queue: str, qcfg: Dict, rp: Dict, token: str, llm: Dict,
                 now: float) -> None:
        try:
            if isinstance(action, StartPod):
                name = f"maestro-{queue}-{uuid.uuid4().hex[:8]}"
                env = {
                    "CP_URL": rp.get("cp_url", ""),
                    "WORKER_TOKEN": token,
                    "WORKER_QUEUE": queue,
                    "IDLE_EXIT_SECONDS": str(qcfg.get("idle_exit_seconds", 10)),
                }
                if queue == "llm":
                    # The engine answers only requests naming its served model, and the pod
                    # cannot read settings, so the string every request will carry is delivered
                    # at create — the entrypoint refuses to boot on a mismatch rather than fail
                    # every turn. The window the control plane budgets against rides along, as
                    # do the engine's tuning flags, so a flag is a settings edit and not an image.
                    env["LLM_MODEL"] = llm["model"]
                    env["LLM_N_CTX"] = str(llm["n_ctx"])
                    env["SGLANG_ARGS_EXTRA"] = llm.get("sglang_args") or ""
                    env["WORKER_SLOTS"] = str(llm.get("slots", 1))
                self._start_pod(name, queue, qcfg, rp, env)
                self._last_scale_up[queue] = now
            elif isinstance(action, TerminatePod):
                self._client.terminate_pod(action.pod_id)
                self._stats.mark_pod_terminated(action.pod_id)
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
        cards differ in what they can serve, which makes the fallback a real downgrade rather
        than a substitution."""
        ids = list(qcfg["gpu_type_ids"])
        # The CUDA floor (`allowed_cuda_versions`) is what the queue's engine needs of the host
        # driver, and every queue has one engine, so an old-driver host is a dead pod on any card
        # and the floor rides the widened ask too (measured 2026-08-02: 3 of 4 draws landed
        # old-driver hosts).
        cuda = qcfg.get("allowed_cuda_versions")
        gpu_asks = [ids[:1], ids] if len(ids) > 1 else [ids]
        # A volume pins its datacenter, so a queue whose weights are copied onto several volumes
        # can be served from several datacenters: each volume is asked in turn, the preferred
        # card first and then the whole list, and the next volume only when a datacenter has
        # refused every card.
        volumes = qcfg.get("network_volume_ids") or [rp.get("network_volume_id", "")]
        attempts = [(v, ask) for v in volumes for ask in gpu_asks]
        refused: List[Dict] = []
        for i, (volume, attempt) in enumerate(attempts):
            try:
                pod = self._client.create_pod(
                    name=name,
                    template_id=qcfg["template_id"],
                    gpu_type_ids=attempt,
                    network_volume_id=volume,
                    env=env,
                    cloud_type=rp.get("cloud_type", "SECURE"),
                    allowed_cuda_versions=cuda,
                )
            except RunPodError as e:
                refused.append({"volume": volume, "gpu_type_ids": attempt, "error": str(e)})
                if i == len(attempts) - 1:
                    self._stats.record_pod_refusal(
                        queue, refusal_kind([r["error"] for r in refused]), refused, str(e))
                    raise
                logger.warning("scale-up %s: create on volume %s with %s (cuda %s) refused (%s) "
                               "— next ask %s on %s", queue, volume, attempt, cuda, e,
                               attempts[i + 1][1], attempts[i + 1][0])
                continue
            logger.info("scale-up %s: created pod %s (%s) on %s, volume %s (cuda %s)",
                        queue, name, pod.get("id"), attempt, volume, cuda)
            self._stats.record_pod_created(queue)
            rate = pod.get("costPerHr")
            self._stats.record_worker_created(
                pod["id"], queue, (pod.get("machine") or {}).get("gpuTypeId"),
                float(rate) if rate is not None else None)
            return
