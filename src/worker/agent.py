"""Worker agent — the pull side of the inference queue.

Claims jobs from the platform's /worker endpoints, runs each payload against a local backend
(worker/handlers.py — verbatim forward for llm, the ComfyUI submit/poll/fetch flow for image,
one POST for mesh), measures execution time, and lands the result. Runs identically on the home
GPU box and inside a RunPod container — the worker dials OUT, so NAT/ephemeral pod networking
never matters. One process per queue, and a queue owns its GPU.

  python -m worker.agent --queue llm   --target http://localhost:8080 --token <t>
  python -m worker.agent --queue image --target http://localhost:8188 --token <t>
  python -m worker.agent --queue mesh  --target http://localhost:8189 --token <t>

The token can also come from WORKER_TOKEN in the environment. SIGTERM finishes the in-flight
job, then exits (spot-preemption friendly; a kill mid-job just lets the lease lapse and the
job requeues).
"""

import argparse
import logging
import os
import queue as queue_mod
import signal
import socket
import subprocess
import threading
import time
import uuid

import requests

from worker.handlers import HANDLERS

logger = logging.getLogger("worker")

HEARTBEAT_INTERVAL = 45.0


def detect_gpu() -> str | None:
    """The card this process is ACTUALLY running on, asked of the device itself.

    RunPod's create-time gpuTypeIds is a preference list, not an assignment, so the type the
    scaler asked for is not the type the pod got — a control plane that records its own request
    reports whatever is first in settings, forever (measured 2026-08-01: 879 prod jobs recorded
    as 5090 while the bill was entirely RTX PRO 4500)."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("gpu detection failed (%s) — jobs will record no gpu_type", e)
        return None
    names = [line.strip() for line in out.splitlines() if line.strip()]
    return names[0] if names else None


class Agent:
    def __init__(self, server: str, target: str, queue: str, token: str, api: str = "chat",
                 worker_id: str = None, gpu_type: str = None, source: str = "local",
                 idle_exit_seconds: float = 0.0):
        self.server = server.rstrip("/")
        self.target = target.rstrip("/")
        # The dialect this worker's target speaks. Known only here — a queue owns its backend.
        self.api = api
        self.queue = queue
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
        self.gpu_type = gpu_type
        self.source = source
        self.idle_exit_seconds = idle_exit_seconds
        self.pod_id = os.environ.get("RUNPOD_POD_ID")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.stopping = False
        # Completions ship in the background so the GPU claims the next job while the previous
        # result (a 20MB+ GLB) is still uploading. One thread, order-preserving.
        self._uploads = queue_mod.Queue()
        self._uploader = threading.Thread(target=self._upload_loop, daemon=True)
        self._uploader.start()

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        r = self.session.post(f"{self.server}{path}", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def claim(self) -> dict | None:
        body = {"queue": self.queue, "worker_id": self.worker_id,
                "gpu_type": self.gpu_type, "source": self.source, "pod_id": self.pod_id}
        if self.idle_exit_seconds:
            # The server long-polls for this window, so a null claim IS the idle verdict —
            # the queue stayed empty for idle_exit_seconds straight. No client-side timer.
            body["wait_seconds"] = self.idle_exit_seconds
        return self._post("/worker/claim", body, timeout=35).get("job")

    def deregister(self) -> None:
        """Best-effort: the control-plane reaper also terminates pods behind stale worker rows,
        so a lost deregister only delays the pod kill, never leaks it."""
        try:
            self._post("/worker/deregister", {"worker_id": self.worker_id}, timeout=10)
        except requests.RequestException as e:
            logger.warning("deregister failed: %s", e)

    def complete(self, job_id: str, result=None, error=None, exec_seconds=0.0) -> None:
        ok = self._post("/worker/complete", {
            "job_id": job_id, "worker_id": self.worker_id, "result": result,
            "error": error, "exec_seconds": exec_seconds, "gpu_type": self.gpu_type,
        }, timeout=120).get("ok")
        if not ok:
            logger.warning("job %s: lease lapsed before completion — result dropped", job_id)

    def _upload_loop(self) -> None:
        while True:
            item = self._uploads.get()
            if item is None:
                return
            job_id, kw = item
            for attempt in (1, 2):
                try:
                    self.complete(job_id, **kw)
                    break
                except requests.RequestException as e:
                    logger.warning("job %s: complete failed (attempt %d/2): %s",
                                   job_id, attempt, e)
                    time.sleep(2)
            # Still failing after the retry: dropped — the lease lapses and the job requeues.

    def _drain_uploads(self) -> None:
        """Flush pending completions and stop the uploader — results must land before
        deregister, or the reaper could kill the pod with a GLB still in flight."""
        self._uploads.put(None)
        self._uploader.join()

    def _heartbeat_until(self, job_id: str, done: threading.Event) -> None:
        while not done.wait(HEARTBEAT_INTERVAL):
            try:
                if not self._post("/worker/heartbeat",
                                  {"job_id": job_id, "worker_id": self.worker_id},
                                  timeout=10).get("ok"):
                    logger.warning("job %s: heartbeat rejected (lease lost)", job_id)
                    return
            except requests.RequestException as e:
                logger.warning("job %s: heartbeat failed: %s", job_id, e)

    def execute(self, job: dict) -> None:
        payload = job["payload"]
        handler = HANDLERS[payload.get("kind", "llm")]
        done = threading.Event()
        hb = threading.Thread(target=self._heartbeat_until, args=(job["id"], done), daemon=True)
        hb.start()
        t0 = time.perf_counter()
        try:
            result, error = handler(self, payload)
            elapsed = time.perf_counter() - t0
            # Enqueued, not sent: the upload rides the remaining lease (the heartbeat stops
            # here), which is plenty — lease_seconds dwarfs one result POST.
            self._uploads.put((job["id"], dict(result=result, error=error,
                                               exec_seconds=elapsed)))
            if error:
                logger.warning("job %s failed: %s", job["id"], error)
            else:
                logger.info("job %s done in %.1fs", job["id"], elapsed)
        except requests.RequestException as e:
            self._uploads.put((job["id"], dict(error=f"Connection error: {e}",
                                               exec_seconds=time.perf_counter() - t0)))
            logger.warning("job %s target unreachable: %s", job["id"], e)
        finally:
            done.set()

    def run(self) -> None:
        logger.info("worker %s pulling queue=%s from %s → %s",
                    self.worker_id, self.queue, self.server, self.target)
        while not self.stopping:
            try:
                job = self.claim()
            except requests.RequestException as e:
                logger.warning("claim failed (%s) — retrying in 5s", e)
                time.sleep(5)
                continue
            if job is None:
                if self.idle_exit_seconds and not self.stopping:
                    logger.info("worker %s idle for %.0fs — exiting",
                                self.worker_id, self.idle_exit_seconds)
                    break
                continue
            self.execute(job)
        self._drain_uploads()
        self.deregister()
        logger.info("worker %s stopped", self.worker_id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Maestro inference worker")
    parser.add_argument("--server", default="http://localhost:8000")
    parser.add_argument("--target", default="http://localhost:1234")
    parser.add_argument("--queue", default="llm")
    parser.add_argument("--api", default="chat", choices=("chat", "responses"),
                        help="wire format the target serves (llm queue only)")
    parser.add_argument("--token", default=os.environ.get("WORKER_TOKEN", ""))
    parser.add_argument("--source", default="local")
    parser.add_argument("--idle-exit-seconds", type=float,
                        default=float(os.environ.get("IDLE_EXIT_SECONDS", "0")),
                        help="Exit 0 after the queue stays empty this long (0 = never, the "
                             "home-box default; autoscaled pods set this to die when drained)")
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("--token (or WORKER_TOKEN) is required")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    # Detected here and nowhere else: there is no --gpu-type, because the card is a fact about the
    # box this process woke up on and nothing outside it is entitled to say otherwise.
    agent = Agent(args.server, args.target, args.queue, args.token, api=args.api,
                  gpu_type=detect_gpu(), source=args.source,
                  idle_exit_seconds=args.idle_exit_seconds)

    def _stop(signum, frame):
        logger.info("signal %s — finishing current job then exiting", signum)
        agent.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
