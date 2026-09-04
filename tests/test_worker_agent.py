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


def _clock(monkeypatch, start=1000.0):
    """A monotonic clock the test advances by hand."""
    t = {"now": start}
    monkeypatch.setattr(worker_agent.time, "monotonic", lambda: t["now"])
    return t


def test_null_claims_exit_only_once_idle_for_the_whole_window(monkeypatch):
    """The server caps one long-poll well under a real idle window, so a single null claim is
    not the verdict: the worker exits when the card has been empty for idle_exit_seconds since
    its last job ended, however many polls that takes."""
    clock = _clock(monkeypatch)
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1",
              idle_exit_seconds=300)
    a.session = MagicMock()
    nulls = {"n": 0}

    def post(url, **kw):
        if url.endswith("/worker/claim"):
            nulls["n"] += 1
            clock["now"] += 25
            return FakeResponse(200, {"job": None})
        return FakeResponse(200, {"ok": True})

    a.session.post.side_effect = post
    a.run()
    assert nulls["n"] == 12
    assert _posts_to(a, "/worker/deregister") == 1


def test_the_idle_clock_restarts_when_a_job_ends(monkeypatch):
    clock = _clock(monkeypatch)
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1",
              idle_exit_seconds=60)
    a.session = MagicMock()
    claims = {"n": 0}

    def post(url, **kw):
        if url.startswith("http://gpu"):
            clock["now"] += 500     # a long job: far past the window, but busy
            return FakeResponse(200, {"output": ["ok"]})
        if url.endswith("/worker/claim"):
            claims["n"] += 1
            if claims["n"] == 1:
                return FakeResponse(200, {"job": {"id": "j1", "payload": {"body": {}}}})
            clock["now"] += 25
            return FakeResponse(200, {"job": None})
        return FakeResponse(200, {"ok": True})

    a.session.post.side_effect = post
    a.run()
    assert claims["n"] == 1 + 3     # 60 s of empty polls AFTER the job, not the job's 500


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


def test_slots_run_jobs_side_by_side():
    """Two slots, two jobs: the second claim is answered while the first job is still running,
    and both are in flight at once — the engine batches, so the worker must hand it both."""
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1", slots=2)
    a.session = MagicMock()
    both_in = threading.Barrier(2, timeout=5)
    handed = ["j1", "j2"]

    def post(url, **kw):
        if url.startswith("http://gpu"):
            both_in.wait()          # hangs unless the other slot's job arrives too
            return FakeResponse(200, {"output": ["ok"]})
        if url.endswith("/worker/claim"):
            if handed:
                return FakeResponse(200, {"job": {"id": handed.pop(0), "payload": {"body": {}}}})
            a.stopping = True
            return FakeResponse(200, {"job": None})
        return FakeResponse(200, {"ok": True})

    a.session.post.side_effect = post
    a.run()
    assert both_in.broken is False
    assert _posts_to(a, "/worker/complete") == 2
    assert _posts_to(a, "/worker/deregister") == 1


def test_an_idle_slot_waits_for_a_busy_sibling():
    """A null claim on one slot while another is mid-job is not the worker's idle verdict: the
    pod stays up, and exits only once every slot has come back empty."""
    a = Agent("http://server", "http://gpu", "llm", "wsecret", worker_id="w1",
              idle_exit_seconds=5, slots=2)
    a.session = MagicMock()
    release = threading.Event()
    claims = {"n": 0}
    null_while_busy = {"n": 0}

    def post(url, **kw):
        if url.startswith("http://gpu"):
            release.wait(5)
            return FakeResponse(200, {"output": ["ok"]})
        if url.endswith("/worker/claim"):
            claims["n"] += 1
            if claims["n"] == 1:
                return FakeResponse(200, {"job": {"id": "j1", "payload": {"body": {}}}})
            with a._busy_lock:
                busy = a._busy
            if busy:
                null_while_busy["n"] += 1
                if null_while_busy["n"] == 2:
                    release.set()   # the sibling saw two empty polls and did not exit
            return FakeResponse(200, {"job": None})
        return FakeResponse(200, {"ok": True})

    a.session.post.side_effect = post
    a.run()
    assert null_while_busy["n"] >= 2
    assert _posts_to(a, "/worker/complete") == 1
    assert _posts_to(a, "/worker/deregister") == 1


# ── worker/handlers.anim_sheet against a faked ComfyUI ───────────────────────────────────────


def _anim_png_bytes(cx, cy):
    import io
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (64, 64), (255, 255, 255))
    ImageDraw.Draw(im).ellipse((cx - 10, cy - 14, cx + 10, cy + 14), fill=(20, 20, 20))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _anim_payload(facings=4):
    import base64
    from tools.comfyui_tools import build_anim_payload
    still = _anim_png_bytes(32, 32)
    anims = [{"name": "walk", "action": "walks in place"},
             {"name": "idle", "action": "breathes"},
             {"name": "attack", "action": "swings"}]
    return build_anim_payload(base64.b64encode(still).decode("ascii"), anims, facings)


def _fake_comfy_anim(monkeypatch):
    """A ComfyUI stand-in: every /prompt call gets its own prompt_id, in call order, and each
    prompt_id's /history lists a handful of moving frames that /view serves as real PNG bytes —
    the turn clip's 22 frames give pick_facings a turntable to cut, each loop's 6 give
    trim/sample something to chew on."""
    from worker import handlers
    calls = {"prompt": 0}
    frame_sets = {}

    def turn_frames():
        """A pinned turntable: front held at both ends, a symmetric back in the middle,
        lopsided profiles either side — matches what pick_facings expects."""
        n = 22
        out = [_anim_png_bytes(32, 32) for _ in range(3)]
        for i in range(3, n - 3):
            if int(n * 0.35) <= i < int(n * 0.65):
                out.append(_anim_png_bytes(32, 20))       # symmetric-ish back stance
            elif i < n // 2:
                out.append(_anim_png_bytes(20, 32))       # right-side profile
            else:
                out.append(_anim_png_bytes(44, 32))       # left-side profile
        out += [_anim_png_bytes(32, 32) for _ in range(3)]
        return out

    def loop_frames():
        return [_anim_png_bytes(30 + i, 32) for i in range(6)]

    frame_sets[0] = turn_frames()
    for i in range(1, 13):
        frame_sets[i] = loop_frames()

    submitted = []

    def post(url, **kw):
        if url.endswith("/upload/image"):
            return FakeResponse(200, {"name": "x.png"})
        if url.endswith("/prompt"):
            idx = calls["prompt"]
            calls["prompt"] += 1
            submitted.append(kw["json"]["prompt"])
            return FakeResponse(200, {"prompt_id": f"p{idx}"})
        return FakeResponse(200, {"ok": True})
    calls["submitted"] = submitted

    def get(url, **kw):
        if "/history/" in url:
            pid = url.rsplit("/", 1)[-1]
            idx = int(pid[1:])
            filenames = [f"{idx}-{i}.png" for i in range(len(frame_sets[idx]))]
            return FakeResponse(200, {pid: {"outputs": {"9": {"images":
                [{"filename": fn} for fn in filenames]}}}})
        if "/view" in url:
            import urllib.parse
            q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
            idx, i = (int(x) for x in q["filename"].replace(".png", "").split("-"))
            return FakeResponse(200, content=frame_sets[idx][i])
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(handlers.safety_vision, "classify",
                        lambda b: {"scores": {"NSFW": 0.01, "SFW": 0.99}})
    return post, get, calls


def test_anim_sheet_builds_a_manifest_with_every_row(monkeypatch):
    from worker import handlers

    a = agent()
    post, get, calls = _fake_comfy_anim(monkeypatch)
    a.session.post.side_effect = post
    a.session.get.side_effect = get

    result, err = handlers.anim_sheet(a, _anim_payload())

    assert err is None
    assert calls["prompt"] == 1 + 3 * 4
    assert "sheet_b64" in result
    assert result["safety"]["scores"]["NSFW"] == 0.01
    manifest = result["manifest"]
    for anim in ("walk", "idle", "attack"):
        rows = manifest["anims"][anim]["rows"]
        assert set(rows) == {"front", "right", "back", "left"}


def test_a_one_facing_anim_skips_the_turntable(monkeypatch):
    """A top-down car or a flat card has one view: no turn clip, one row per anim, and the
    prompts carry no facing phrase."""
    from worker import handlers

    a = agent()
    post, get, calls = _fake_comfy_anim(monkeypatch)
    a.session.post.side_effect = post
    a.session.get.side_effect = get
    payload = _anim_payload(facings=1)
    # the fake's first prompt is shaped like a turntable; with no turn, every prompt is a loop
    result, err = handlers.anim_sheet(a, payload)

    assert err is None
    assert calls["prompt"] == 3
    manifest = result["manifest"]
    assert manifest["dirs"] == ["front"]
    for anim in ("walk", "idle", "attack"):
        assert manifest["anims"][anim]["rows"] == {"front": manifest["anims"][anim]["rows"]["front"]}
    for wf in calls["submitted"]:
        assert "facing" not in wf["5"]["inputs"]["prompt"]
        assert wf["5"]["inputs"]["length"] == 22
    assert result["facings"] == {"front": 0}


def test_anim_sheet_runs_the_turn_at_its_own_step_count(monkeypatch):
    """A loop survives the workflow's few steps; a turn dissolves at them, so the payload names
    the turn's steps and only the turn gets them."""
    from worker import handlers

    a = agent()
    post, get, calls = _fake_comfy_anim(monkeypatch)
    a.session.post.side_effect = post
    a.session.get.side_effect = get
    payload = _anim_payload()

    handlers.anim_sheet(a, payload)

    turn, *loops = calls["submitted"]
    assert turn["9"]["inputs"]["steps"] == payload["turn"]["steps"]
    assert turn["5"]["inputs"]["length"] == payload["turn"]["length"]
    assert {wf["9"]["inputs"]["steps"] for wf in loops} == \
        {payload["loop"]["workflow"]["9"]["inputs"]["steps"]}
    assert payload["turn"]["steps"] != payload["loop"]["workflow"]["9"]["inputs"]["steps"]


def test_anim_sheet_reports_a_comfy_error_without_raising(monkeypatch):
    from worker import handlers

    a = agent()
    a.session.post.side_effect = lambda url, **kw: FakeResponse(500, text="gpu died")

    result, err = handlers.anim_sheet(a, _anim_payload())

    assert result is None
    assert "gpu died" in err
