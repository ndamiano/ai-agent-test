"""Worker agent — claims, forwards the payload to the local target without leaking the worker
token, and lands result/error with measured exec time."""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests

from worker.agent import Agent


class _Resp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def _agent():
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1")
    a.session = MagicMock()
    return a


def test_execute_forwards_body_and_strips_the_worker_token():
    a = _agent()
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if url.startswith("http://gpu"):
            return _Resp(200, {"output": ["ok"]})
        return _Resp(200, {"ok": True})

    a.session.post.side_effect = post
    a.execute({"id": "j1", "payload": {"kind": "llm", "body": {"model": "m", "messages": []}}})
    a._drain_uploads()   # completions ship in the background — flush before asserting

    (gpu_url, gpu_kw) = calls[0]
    assert gpu_url == "http://gpu/v1/chat/completions"
    # the worker translated the canonical body for its dialect before sending
    assert gpu_kw["json"]["model"] == "m" and gpu_kw["json"]["messages"] == []
    assert gpu_kw["headers"]["Authorization"] is None   # platform token never reaches the GPU

    (srv_url, srv_kw) = calls[1]
    assert srv_url == "http://server/worker/complete"
    body = srv_kw["json"]
    assert body["job_id"] == "j1" and body["result"] == {"output": ["ok"]}
    assert body["error"] is None and body["exec_seconds"] > 0


def test_execute_reports_upstream_errors():
    a = _agent()

    def post(url, **kw):
        if url.startswith("http://gpu"):
            return _Resp(500, text="failed to load")
        return _Resp(200, {"ok": True})

    a.session.post.side_effect = post
    a.execute({"id": "j1", "payload": {"body": {}}})
    a._drain_uploads()

    body = a.session.post.call_args_list[-1].kwargs["json"]
    assert body["result"] is None
    assert "failed to load" in body["error"]


def test_execute_returns_while_the_result_is_still_uploading():
    """The pipelining contract: the GPU is free to claim the next job the moment the handler
    returns — the completion POST happens on the uploader thread."""
    a = _agent()
    gate = threading.Event()
    completed = threading.Event()

    def post(url, **kw):
        if url.startswith("http://gpu"):
            return _Resp(200, {"output": ["ok"]})
        gate.wait(5)   # a slow /worker/complete (big GLB on a thin uplink)
        completed.set()
        return _Resp(200, {"ok": True})

    a.session.post.side_effect = post
    a.execute({"id": "j1", "payload": {"path": "/x", "body": {}}})
    assert not completed.is_set()   # execute returned; the upload is still in flight
    gate.set()
    a._drain_uploads()
    assert completed.is_set()


def test_run_drains_uploads_before_deregister():
    a = _agent()
    order = []

    def post(url, **kw):
        if url.startswith("http://gpu"):
            return _Resp(200, {"output": ["ok"]})
        if url.endswith("/worker/claim"):
            a.stopping = True
            return _Resp(200, {"job": {"id": "j1", "payload": {"path": "/x", "body": {}}}})
        order.append(url.rsplit("/", 1)[-1])
        return _Resp(200, {"ok": True})

    a.session.post.side_effect = post
    a.run()
    assert order == ["complete", "deregister"]


def _posts_to(a, path):
    return sum(1 for c in a.session.post.call_args_list if c.args[0].endswith(path))


def test_claim_body_carries_pod_id_and_wait_seconds(monkeypatch):
    monkeypatch.setenv("RUNPOD_POD_ID", "pod-abc")
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1",
              idle_exit_seconds=10)
    a.session = MagicMock()
    a.session.post.return_value = _Resp(200, {"job": None})
    a.claim()
    body = a.session.post.call_args.kwargs["json"]
    assert body["pod_id"] == "pod-abc"
    assert body["wait_seconds"] == 10


def test_idle_exit_zero_sends_no_wait_seconds():
    a = _agent()
    a.session.post.return_value = _Resp(200, {"job": None})
    a.claim()
    assert "wait_seconds" not in a.session.post.call_args.kwargs["json"]


def test_null_claim_with_idle_exit_deregisters_and_stops():
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1",
              idle_exit_seconds=5)
    a.session = MagicMock()
    a.session.post.return_value = _Resp(200, {"job": None})
    a.run()   # returns instead of looping forever
    assert _posts_to(a, "/worker/deregister") == 1


def test_idle_exit_zero_never_exits_on_a_null_claim():
    a = _agent()
    calls = {"n": 0}

    def post(url, **kw):
        if url.endswith("/worker/claim"):
            calls["n"] += 1
            if calls["n"] >= 3:
                a.stopping = True
            return _Resp(200, {"job": None})
        return _Resp(200, {"ok": True})

    a.session.post.side_effect = post
    a.run()
    assert calls["n"] >= 3   # null claims did not stop it; only stopping did


def test_sigterm_drain_deregisters():
    a = _agent()
    a.session.post.return_value = _Resp(200, {"ok": True})
    a.stopping = True
    a.run()
    assert _posts_to(a, "/worker/deregister") == 1


def test_deregister_is_best_effort():
    a = _agent()
    a.session.post.side_effect = requests.ConnectionError("cp down")
    a.deregister()   # swallowed, not raised


def test_comfy_image_uploads_init_images_before_submit():
    import base64
    from worker import handlers

    a = _agent()
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if url.endswith("/upload/image"):
            return _Resp(200, {"name": "init_x.png"})
        return _Resp(200, {"prompt_id": "p1"})

    a.session.post.side_effect = post
    a.session.get.return_value = _Resp(200, {"p1": {"outputs": {}}})

    result, err = handlers.comfy_image(a, {
        "kind": "comfy_image", "workflow": {},
        "uploads": [{"name": "init_x.png", "b64": base64.b64encode(b"png").decode("ascii")}]})

    assert err is None
    up_url, up_kw = calls[0]
    assert up_url == "http://gpu/upload/image"
    assert up_kw["files"]["image"][0] == "init_x.png"
    assert up_kw["files"]["image"][1] == b"png"
    assert calls[1][0] == "http://gpu/prompt"


def test_comfy_image_upload_failure_never_submits():
    from worker import handlers

    a = _agent()
    calls = []

    def post(url, **kw):
        calls.append(url)
        return _Resp(500, text="disk full")

    a.session.post.side_effect = post
    result, err = handlers.comfy_image(a, {
        "kind": "comfy_image", "workflow": {}, "uploads": [{"name": "i.png", "b64": "aGk="}]})

    assert result is None and "/upload/image" in err
    assert calls == ["http://gpu/upload/image"]
