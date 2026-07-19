"""The worker-pull queue: store-level claim/lease/complete semantics, the /worker HTTP
endpoints (token-gated, outside the user auth gate), and the QueueConnector transport
end-to-end against a fake in-process worker."""

import sys
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")
    # The global LLM rate limiter is a shared token bucket — earlier suite tests can drain it,
    # turning connector calls into rate-limit errors here. Refill it.
    from llm_clients.rate_limiter import get_llm_rate_limiter
    get_llm_rate_limiter().reset()


# ── store semantics ───────────────────────────────────────────────────────────
def test_claim_is_fifo_and_exclusive():
    a = store.enqueue_job("llm", {"n": 1})
    b = store.enqueue_job("llm", {"n": 2})
    j1 = store.claim_job("llm", "w1", lease_seconds=60)
    j2 = store.claim_job("llm", "w2", lease_seconds=60)
    assert (j1["id"], j2["id"]) == (a, b)
    assert store.claim_job("llm", "w3", lease_seconds=60) is None


def test_queues_are_separate():
    store.enqueue_job("image", {"prompt": "cat"})
    assert store.claim_job("llm", "w1", lease_seconds=60) is None
    assert store.claim_job("image", "w1", lease_seconds=60)["payload"] == {"prompt": "cat"}


def test_expired_lease_requeues_and_stale_result_is_dropped():
    job_id = store.enqueue_job("llm", {"n": 1})
    store.claim_job("llm", "w1", lease_seconds=0.01)
    time.sleep(0.02)
    rejig = store.claim_job("llm", "w2", lease_seconds=60)   # sweep requeues, w2 takes it
    assert rejig["id"] == job_id
    # w1 comes back late — its completion must not clobber w2's claim.
    assert store.complete_job(job_id, "w1", {"stale": True}, None, 1.0) is False
    assert store.complete_job(job_id, "w2", {"fresh": True}, None, 2.0) is True
    assert store.get_job(job_id)["result"] == {"fresh": True}


def test_heartbeat_extends_only_the_owners_lease():
    job_id = store.enqueue_job("llm", {})
    store.claim_job("llm", "w1", lease_seconds=60)
    assert store.heartbeat_job(job_id, "w1", lease_seconds=60) is True
    assert store.heartbeat_job(job_id, "intruder", lease_seconds=60) is False


def test_complete_debits_the_games_budget_and_worker_busy():
    store.create_game("g1", "u1")
    store.worker_seen("w1", "llm")
    job_id = store.enqueue_job("llm", {}, game_id="g1")
    store.claim_job("llm", "w1", lease_seconds=60)
    store.complete_job(job_id, "w1", {"out": 1}, None, exec_seconds=12.5)
    assert store.game("g1")["seconds_used"] == 12.5
    job = store.get_job(job_id)
    assert (job["status"], job["exec_seconds"]) == ("done", 12.5)


def test_failed_job_carries_the_error():
    job_id = store.enqueue_job("llm", {})
    store.claim_job("llm", "w1", lease_seconds=60)
    store.complete_job(job_id, "w1", None, "Status 500: boom", 3.0)
    job = store.get_job(job_id)
    assert (job["status"], job["error"]) == ("failed", "Status 500: boom")


# ── /worker endpoints ─────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    from api.routers import workqueue as wq
    monkeypatch.setattr(wq, "_queue_settings",
                        lambda: {"token": "wsecret", "lease_seconds": 60})
    monkeypatch.setattr(wq, "CLAIM_LONG_POLL_SECONDS", 0.2)
    from api.app import app
    return TestClient(app)


def _hdr(token="wsecret"):
    return {"Authorization": f"Bearer {token}"}


def test_worker_endpoints_refuse_without_the_token(client):
    body = {"queue": "llm", "worker_id": "w1"}
    assert client.post("/worker/claim", json=body).status_code == 403
    assert client.post("/worker/claim", json=body, headers=_hdr("wrong")).status_code == 403


def test_unconfigured_token_fails_closed(client, monkeypatch):
    from api.routers import workqueue as wq
    monkeypatch.setattr(wq, "_queue_settings", lambda: {"token": ""})
    r = client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1"},
                    headers=_hdr(""))
    assert r.status_code == 403


def test_claim_execute_complete_over_http(client):
    store.create_game("g1", "u1")
    store.enqueue_job("llm", {"path": "/v1/responses", "body": {"model": "m"}}, game_id="g1")

    r = client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1"}, headers=_hdr())
    job = r.json()["job"]
    assert job["payload"]["body"] == {"model": "m"}

    hb = client.post("/worker/heartbeat", json={"job_id": job["id"], "worker_id": "w1"},
                     headers=_hdr())
    assert hb.json()["ok"] is True

    done = client.post("/worker/complete", json={
        "job_id": job["id"], "worker_id": "w1", "result": {"output": []},
        "exec_seconds": 4.5}, headers=_hdr())
    assert done.json()["ok"] is True
    assert store.game("g1")["seconds_used"] == 4.5


def test_empty_claim_long_polls_then_returns_null(client):
    r = client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1"}, headers=_hdr())
    assert r.json() == {"job": None}


# ── QueueConnector end-to-end with a fake worker ──────────────────────────────
def _fake_worker(stop, respond):
    """Claim from the store directly and complete with `respond(payload)`."""
    while not stop.is_set():
        job = store.claim_job("llm", "fake", lease_seconds=60)
        if job is None:
            time.sleep(0.01)
            continue
        result, error = respond(job["payload"])
        store.complete_job(job["id"], "fake", result, error, exec_seconds=1.0)


def _connector(timeout=10):
    from llm_clients.queue_connector import QueueConnector
    return QueueConnector(base_url="http://unused", model="test-model",
                          job_timeout_seconds=timeout)


@pytest.fixture
def fake_worker():
    stop = threading.Event()
    holder = {}

    def start(respond):
        t = threading.Thread(target=_fake_worker, args=(stop, respond), daemon=True)
        holder["t"] = t
        t.start()

    yield start
    stop.set()
    if "t" in holder:
        holder["t"].join(timeout=2)


def test_queue_connector_round_trip(fake_worker):
    def respond(payload):
        assert payload["path"] == "/v1/responses"
        assert payload["body"]["model"] == "test-model"
        return {"output": [{"type": "message", "content":
                            [{"type": "output_text", "text": "hello"}]}],
                "usage": {"input_tokens": 3, "output_tokens": 1}}, None

    fake_worker(respond)
    result = _connector().generate_with_tools(
        [{"role": "user", "content": "hi"}], [])
    assert result["choices"][0]["message"]["content"] == "hello"
    assert result["usage"]["completion_tokens"] == 1


def test_queue_connector_attributes_jobs_to_the_run_scope(fake_worker):
    from tools.execution_context import run_scope
    store.create_game("g9", "u1")
    fake_worker(lambda p: ({"output": []}, None))
    with run_scope("g9"):
        _connector().generate_with_tools([{"role": "user", "content": "hi"}], [])
    assert store.game("g9")["seconds_used"] == 1.0


def test_queue_connector_surfaces_worker_errors(fake_worker):
    fake_worker(lambda p: (None, "Status 500: model exploded"))
    result = _connector().generate_with_tools([{"role": "user", "content": "hi"}], [])
    assert "model exploded" in result["error"]


def test_queue_connector_times_out_without_a_worker():
    result = _connector(timeout=0.3).generate_with_tools(
        [{"role": "user", "content": "hi"}], [])
    assert "timed out" in result["error"]
