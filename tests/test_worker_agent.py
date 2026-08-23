"""Worker agent — claims, forwards the payload to the local target without leaking the worker
token, and lands result/error with measured exec time."""

import subprocess
import threading
from unittest.mock import MagicMock

import pytest
import requests

import worker.agent as worker_agent
from worker.agent import Agent, detect_gpu
from fakes import FakeResponse, agent


def test_execute_forwards_body_and_strips_the_worker_token():
    a = agent()
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if url.startswith("http://gpu"):
            return FakeResponse(200, {"output": ["ok"]})
        return FakeResponse(200, {"ok": True})

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
    a = agent()

    def post(url, **kw):
        if url.startswith("http://gpu"):
            return FakeResponse(500, text="failed to load")
        return FakeResponse(200, {"ok": True})

    a.session.post.side_effect = post
    a.execute({"id": "j1", "payload": {"body": {}}})
    a._drain_uploads()

    body = a.session.post.call_args_list[-1].kwargs["json"]
    assert body["result"] is None
    assert "failed to load" in body["error"]


def test_execute_returns_while_the_result_is_still_uploading():
    """The pipelining contract: the GPU is free to claim the next job the moment the handler
    returns — the completion POST happens on the uploader thread."""
    a = agent()
    gate = threading.Event()
    completed = threading.Event()

    def post(url, **kw):
        if url.startswith("http://gpu"):
            return FakeResponse(200, {"output": ["ok"]})
        gate.wait(5)   # a slow /worker/complete (big GLB on a thin uplink)
        completed.set()
        return FakeResponse(200, {"ok": True})

    a.session.post.side_effect = post
    a.execute({"id": "j1", "payload": {"path": "/x", "body": {}}})
    assert not completed.is_set()   # execute returned; the upload is still in flight
    gate.set()
    a._drain_uploads()
    assert completed.is_set()


def test_run_drains_uploads_before_deregister():
    a = agent()
    order = []

    def post(url, **kw):
        if url.startswith("http://gpu"):
            return FakeResponse(200, {"output": ["ok"]})
        if url.endswith("/worker/claim"):
            a.stopping = True
            return FakeResponse(200, {"job": {"id": "j1", "payload": {"path": "/x", "body": {}}}})
        order.append(url.rsplit("/", 1)[-1])
        return FakeResponse(200, {"ok": True})

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
    a.session.post.return_value = FakeResponse(200, {"job": None})
    a.claim()
    body = a.session.post.call_args.kwargs["json"]
    assert body["pod_id"] == "pod-abc"
    assert body["wait_seconds"] == 10


def test_idle_exit_zero_sends_no_wait_seconds():
    a = agent()
    a.session.post.return_value = FakeResponse(200, {"job": None})
    a.claim()
    assert "wait_seconds" not in a.session.post.call_args.kwargs["json"]


def test_null_claim_with_idle_exit_deregisters_and_stops():
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1",
              idle_exit_seconds=5)
    a.session = MagicMock()
    a.session.post.return_value = FakeResponse(200, {"job": None})
    a.run()   # returns instead of looping forever
    assert _posts_to(a, "/worker/deregister") == 1


def test_idle_exit_zero_never_exits_on_a_null_claim():
    a = agent()
    calls = {"n": 0}

    def post(url, **kw):
        if url.endswith("/worker/claim"):
            calls["n"] += 1
            if calls["n"] >= 3:
                a.stopping = True
            return FakeResponse(200, {"job": None})
        return FakeResponse(200, {"ok": True})

    a.session.post.side_effect = post
    a.run()
    assert calls["n"] >= 3   # null claims did not stop it; only stopping did


def test_sigterm_drain_deregisters():
    a = agent()
    a.session.post.return_value = FakeResponse(200, {"ok": True})
    a.stopping = True
    a.run()
    assert _posts_to(a, "/worker/deregister") == 1


def test_deregister_is_best_effort():
    a = agent()
    a.session.post.side_effect = requests.ConnectionError("cp down")
    a.deregister()   # swallowed, not raised


def test_comfy_image_uploads_init_images_before_submit():
    import base64
    from worker import handlers

    a = agent()
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if url.endswith("/upload/image"):
            return FakeResponse(200, {"name": "init_x.png"})
        return FakeResponse(200, {"prompt_id": "p1"})

    a.session.post.side_effect = post
    a.session.get.return_value = FakeResponse(200, {"p1": {"outputs": {}}})

    result, err = handlers.comfy_image(a, {
        "kind": "comfy_image", "workflow": {},
        "uploads": [{"name": "init_x.png", "b64": base64.b64encode(b"png").decode("ascii")}]})

    assert err is None
    up_url, up_kw = calls[0]
    assert up_url == "http://gpu/upload/image"
    assert up_kw["files"]["image"][0] == "init_x.png"
    assert up_kw["files"]["image"][1] == b"png"
    assert calls[1][0] == "http://gpu/prompt"


def test_comfy_image_attaches_a_safety_verdict_to_every_image(monkeypatch):
    from worker import handlers

    a = agent()
    a.session.post.return_value = FakeResponse(200, {"prompt_id": "p1"})
    outputs = {"9": {"images": [{"filename": "out.png"}]}}

    def get(url, **kw):
        if "/history/" in url:
            return FakeResponse(200, {"p1": {"outputs": outputs}})
        return FakeResponse(200, content=b"pngbytes")

    a.session.get.side_effect = get
    seen = []
    monkeypatch.setattr(handlers.safety_vision, "classify",
                        lambda b: seen.append(b) or {"scores": {"NSFW": 0.01, "SFW": 0.99}})

    result, err = handlers.comfy_image(a, {"kind": "comfy_image", "workflow": {}})
    assert err is None
    assert seen == [b"pngbytes"]   # scored from the fetched bytes, before base64
    assert result["images"][0]["safety"] == {"scores": {"NSFW": 0.01, "SFW": 0.99}}


def test_an_unloadable_classifier_reports_an_error_not_scores(monkeypatch):
    """The worker fails open (it still ships the render); the control plane's no-scores refusal is
    what closes it."""
    from worker import safety_vision

    monkeypatch.setattr(safety_vision, "_loaded", None)
    monkeypatch.setenv("SAFETY_MODEL_DIR", "/nonexistent")
    out = safety_vision.classify(b"not an image")
    assert "error" in out and "scores" not in out
    # The failure is cached — the next call answers without retrying the load.
    assert "error" in safety_vision.classify(b"x")


def test_comfy_image_upload_failure_never_submits():
    from worker import handlers

    a = agent()
    calls = []

    def post(url, **kw):
        calls.append(url)
        return FakeResponse(500, text="disk full")

    a.session.post.side_effect = post
    result, err = handlers.comfy_image(a, {
        "kind": "comfy_image", "workflow": {}, "uploads": [{"name": "i.png", "b64": "aGk="}]})

    assert result is None and "/upload/image" in err
    assert calls == ["http://gpu/upload/image"]


def test_claim_carries_the_card():
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1",
              gpu_type="NVIDIA RTX PRO 4500 Blackwell")
    a.session = MagicMock()
    a.session.post.return_value = FakeResponse(200, {"job": None})
    a.claim()
    assert a.session.post.call_args.kwargs["json"]["gpu_type"] == "NVIDIA RTX PRO 4500 Blackwell"


def test_the_card_is_read_off_the_device_the_worker_woke_up_on(monkeypatch):
    """Never what someone asked RunPod for: gpuTypeIds is a preference list, and recording the
    request reported 5090 for 879 prod jobs billed as RTX PRO 4500."""
    captured = {}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def run(self):
            pass

    monkeypatch.setattr(worker_agent, "Agent", FakeAgent)
    monkeypatch.setattr(worker_agent, "detect_gpu", lambda: "NVIDIA RTX PRO 4500 Blackwell")
    worker_agent.main(["--token", "wsecret"])
    assert captured["gpu_type"] == "NVIDIA RTX PRO 4500 Blackwell"


def test_no_one_outside_the_box_may_name_the_card():
    with pytest.raises(SystemExit):
        worker_agent.main(["--token", "wsecret", "--gpu-type", "NVIDIA GeForce RTX 5090"])


def test_detect_gpu_reads_the_first_device_name(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, "NVIDIA RTX PRO 4500 Blackwell\n", ""))
    assert detect_gpu() == "NVIDIA RTX PRO 4500 Blackwell"


def test_detect_gpu_survives_a_box_without_nvidia_smi(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(subprocess, "run", boom)
    assert detect_gpu() is None   # a CPU box registers, it just records no card
