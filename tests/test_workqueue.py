"""The worker-pull queue: store-level claim/lease/complete semantics, the /worker HTTP
endpoints (token-gated, outside the user auth gate), and the QueueConnector transport
end-to-end against a fake in-process worker."""

import sys
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from api.app import app

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import base64

from api.routers import workqueue as wq
from db import store
from llm_clients.queue_connector import QueueConnector
from llm_clients.rate_limiter import get_llm_rate_limiter
from tools.execution_context import run_scope


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")
    # The global LLM rate limiter is a shared token bucket — earlier suite tests can drain it,
    # turning connector calls into rate-limit errors here. Refill it.
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
    monkeypatch.setattr(wq, "_queue_settings",
                        lambda: {"token": "wsecret", "lease_seconds": 60})
    monkeypatch.setattr(wq, "CLAIM_LONG_POLL_SECONDS", 0.2)
    return TestClient(app)


def _hdr(token="wsecret"):
    return {"Authorization": f"Bearer {token}"}


def test_worker_endpoints_refuse_without_the_token(client):
    body = {"queue": "llm", "worker_id": "w1"}
    assert client.post("/worker/claim", json=body).status_code == 403
    assert client.post("/worker/claim", json=body, headers=_hdr("wrong")).status_code == 403


def test_unconfigured_token_fails_closed(client, monkeypatch):
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


def test_complete_offloads_the_glb_to_the_blob_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "_blob_dir", lambda: tmp_path / "blobs")

    jid = store.enqueue_job("mesh", {"kind": "trellis_mesh"})
    client.post("/worker/claim", json={"queue": "mesh", "worker_id": "w1"}, headers=_hdr())
    glb = b"glTF-binary-bytes"
    r = client.post("/worker/complete", json={
        "job_id": jid, "worker_id": "w1",
        "result": {"glb_b64": base64.b64encode(glb).decode("ascii")}}, headers=_hdr())
    assert r.json()["ok"] is True

    job = store.get_job(jid)
    assert "glb_b64" not in job["result"]
    blob = Path(job["result"]["glb_file"])
    assert blob == tmp_path / "blobs" / f"{jid}.glb"
    assert blob.read_bytes() == glb


def test_stale_glb_completion_removes_its_blob(client, tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "_blob_dir", lambda: tmp_path / "blobs")

    jid = store.enqueue_job("mesh", {"kind": "trellis_mesh"})
    client.post("/worker/claim", json={"queue": "mesh", "worker_id": "w1"}, headers=_hdr())
    r = client.post("/worker/complete", json={
        "job_id": jid, "worker_id": "not-the-claimant",
        "result": {"glb_b64": base64.b64encode(b"x").decode("ascii")}}, headers=_hdr())
    assert r.json()["ok"] is False
    assert list((tmp_path / "blobs").glob("*")) == []


def test_complete_offloads_each_image_to_the_blob_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "_blob_dir", lambda: tmp_path / "blobs")

    jid = store.enqueue_job("image", {"kind": "comfy_image"})
    client.post("/worker/claim", json={"queue": "image", "worker_id": "w1"}, headers=_hdr())
    r = client.post("/worker/complete", json={
        "job_id": jid, "worker_id": "w1",
        "result": {"prompt_id": "p1", "images": [
            {"filename": "a.png", "b64": base64.b64encode(b"png-a").decode("ascii")},
            {"filename": "b.png", "b64": base64.b64encode(b"png-b").decode("ascii")},
        ]}}, headers=_hdr())
    assert r.json()["ok"] is True

    imgs = store.get_job(jid)["result"]["images"]
    assert all("b64" not in img for img in imgs)
    assert [img["filename"] for img in imgs] == ["a.png", "b.png"]
    assert Path(imgs[0]["file"]).read_bytes() == b"png-a"
    assert Path(imgs[1]["file"]).read_bytes() == b"png-b"


def test_glb_completion_refuses_a_path_shaped_job_id(client, tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "_blob_dir", lambda: tmp_path / "blobs")
    r = client.post("/worker/complete", json={
        "job_id": "../../etc/passwd", "worker_id": "w1",
        "result": {"glb_b64": base64.b64encode(b"x").decode("ascii")}}, headers=_hdr())
    assert r.status_code == 400


def test_empty_claim_long_polls_then_returns_null(client):
    r = client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1"}, headers=_hdr())
    assert r.json() == {"job": None}


def test_wait_seconds_shortens_the_long_poll_window(client, monkeypatch):
    monkeypatch.setattr(wq, "CLAIM_LONG_POLL_SECONDS", 30.0)
    t0 = time.time()
    r = client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1",
                                           "wait_seconds": 0.1}, headers=_hdr())
    assert r.json() == {"job": None}
    assert time.time() - t0 < 5   # honored the request, not the 30s server max


def test_wait_seconds_is_capped_at_the_server_max(client):
    # server max is 0.2 in this fixture; asking for 60 must not hold the request for 60s
    t0 = time.time()
    r = client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1",
                                           "wait_seconds": 60}, headers=_hdr())
    assert r.json() == {"job": None}
    assert time.time() - t0 < 5


def test_claim_records_the_pod_id(client):
    client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1",
                                       "pod_id": "pod-1"}, headers=_hdr())
    assert store.live_workers("llm", 60)[0]["pod_id"] == "pod-1"


def test_heartbeat_bumps_worker_last_seen(client):
    store.enqueue_job("llm", {})
    r = client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1"}, headers=_hdr())
    job = r.json()["job"]
    with store._db() as conn:
        conn.execute("UPDATE workers SET last_seen_at = last_seen_at - 999 WHERE id = 'w1'")
    assert store.live_workers("llm", 60) == []
    client.post("/worker/heartbeat", json={"job_id": job["id"], "worker_id": "w1"},
                headers=_hdr())
    assert [w["id"] for w in store.live_workers("llm", 60)] == ["w1"]


def test_deregister_terminates_the_worker_row(client):
    client.post("/worker/claim", json={"queue": "llm", "worker_id": "w1",
                                       "pod_id": "pod-1"}, headers=_hdr())
    r = client.post("/worker/deregister", json={"worker_id": "w1"}, headers=_hdr())
    assert r.json() == {"ok": True}
    assert store.live_workers("llm", 60) == []
    assert store.terminated_workers_with_pods("llm")[0]["id"] == "w1"


def test_deregister_requires_the_token(client):
    assert client.post("/worker/deregister", json={"worker_id": "w1"}).status_code == 403


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
