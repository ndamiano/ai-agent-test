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
import signal
import socket
import threading
import time
import uuid

import requests

from worker.handlers import HANDLERS

logger = logging.getLogger("worker")

HEARTBEAT_INTERVAL = 45.0


class Agent:
    def __init__(self, server: str, target: str, queue: str, token: str,
                 worker_id: str = None, gpu_type: str = None, source: str = "local"):
        self.server = server.rstrip("/")
        self.target = target.rstrip("/")
        self.queue = queue
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
        self.gpu_type = gpu_type
        self.source = source
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.stopping = False

    # ── server API ────────────────────────────────────────────────────────────
    def _post(self, path: str, body: dict, timeout: float) -> dict:
        r = self.session.post(f"{self.server}{path}", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def claim(self) -> dict | None:
        body = {"queue": self.queue, "worker_id": self.worker_id,
                "gpu_type": self.gpu_type, "source": self.source}
        return self._post("/worker/claim", body, timeout=35).get("job")

    def complete(self, job_id: str, result=None, error=None, exec_seconds=0.0) -> None:
        ok = self._post("/worker/complete", {
            "job_id": job_id, "worker_id": self.worker_id, "result": result,
            "error": error, "exec_seconds": exec_seconds, "gpu_type": self.gpu_type,
        }, timeout=30).get("ok")
        if not ok:
            logger.warning("job %s: lease lapsed before completion — result dropped", job_id)

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

    # ── execution ─────────────────────────────────────────────────────────────
    def execute(self, job: dict) -> None:
        payload = job["payload"]
        handler = HANDLERS[payload.get("kind", "http")]
        done = threading.Event()
        hb = threading.Thread(target=self._heartbeat_until, args=(job["id"], done), daemon=True)
        hb.start()
        t0 = time.perf_counter()
        try:
            result, error = handler(self, payload)
            elapsed = time.perf_counter() - t0
            self.complete(job["id"], result=result, error=error, exec_seconds=elapsed)
            if error:
                logger.warning("job %s failed: %s", job["id"], error)
            else:
                logger.info("job %s done in %.1fs", job["id"], elapsed)
        except requests.RequestException as e:
            self.complete(job["id"], error=f"Connection error: {e}",
                          exec_seconds=time.perf_counter() - t0)
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
                continue
            self.execute(job)
        logger.info("worker %s stopped", self.worker_id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Maestro inference worker")
    parser.add_argument("--server", default="http://localhost:8000")
    parser.add_argument("--target", default="http://localhost:1234")
    parser.add_argument("--queue", default="llm")
    parser.add_argument("--token", default=os.environ.get("WORKER_TOKEN", ""))
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--gpu-type", default=None)
    parser.add_argument("--source", default="local")
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("--token (or WORKER_TOKEN) is required")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    agent = Agent(args.server, args.target, args.queue, args.token,
                  worker_id=args.worker_id, gpu_type=args.gpu_type, source=args.source)

    def _stop(signum, frame):
        logger.info("signal %s — finishing current job then exiting", signum)
        agent.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
