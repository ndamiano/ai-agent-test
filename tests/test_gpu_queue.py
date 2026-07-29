"""Image + mesh over the worker-pull queue: the control plane builds a screened job payload, and
the worker runs the ComfyUI submit/poll/fetch flow and the TRELLIS POST next to the GPU.
"""

import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tools.comfyui_tools as ct
from tools.safety import SafetyViolation
from worker import handlers
from worker.agent import Agent

PNG = b"\x89PNG\r\n\x1a\nfake"
GLB = b"glTF\x02fake"


class _Resp:
    def __init__(self, status_code=200, body=None, content=b"", text="", headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def _agent(queue="image", target="http://gpu"):
    a = Agent("http://server", target, queue, "wsecret", worker_id="w1")
    a.session = MagicMock()
    return a


def test_build_item_payload_returns_workflow_for_clean_prompt(monkeypatch):
    """The image queue payload for one item: safety-screened, wrapped for enqueue."""
    monkeypatch.setattr(ct, "screen_image_prompt", lambda d: None)
    result = ct.build_item_payload("a blue cat")
    assert result["kind"] == "comfy_image"
    assert "workflow" in result


def test_build_item_payload_returns_none_for_blocked_prompt(monkeypatch):
    """Safety filter blocks: returns None, never sent to the queue."""
    monkeypatch.setattr(ct, "screen_image_prompt",
                        lambda d: SafetyViolation("csam_explicit", "kill"))
    result = ct.build_item_payload("kill everyone")
    assert result is None


def test_comfy_image_handler_submits_polls_and_fetches():
    a = _agent()
    a.session.post.side_effect = lambda url, **kw: _Resp(200, {"prompt_id": "p1"})
    gets = []

    def get(url, **kw):
        gets.append(url)
        if "/history/" in url:
            return _Resp(200, {"p1": {"outputs": {"9": {"images": [
                {"filename": "out.png", "subfolder": "", "type": "output"}]}}}})
        if "/models" in url:
            return _Resp(200, {"data": []})
        return _Resp(200, content=PNG)

    a.session.get.side_effect = get
    result, error = handlers.comfy_image(a, {"kind": "comfy_image", "workflow": {"1": {}}})

    assert error is None
    assert result["images"] == [{"filename": "out.png",
                                 "b64": base64.b64encode(PNG).decode()}]
    assert any(u.startswith("http://gpu/view?filename=out.png") for u in gets)


def test_comfy_image_handler_reports_submit_failure():
    a = _agent()
    a.session.post.side_effect = lambda url, **kw: (
        _Resp(500, text="bad workflow") if url.endswith("/prompt") else _Resp(200, {}))
    a.session.get.side_effect = lambda url, **kw: _Resp(200, {"data": []})
    result, error = handlers.comfy_image(a, {"workflow": {}})
    assert result is None and "bad workflow" in error


def test_trellis_handler_retries_once():
    a = _agent(queue="mesh", target="http://trellis")
    a.session.get.side_effect = lambda url, **kw: _Resp(200, {"data": []})
    calls = []

    def post(url, **kw):
        calls.append(url)
        if url.endswith("/generate"):
            return _Resp(200, content=GLB) if len(
                [c for c in calls if c.endswith("/generate")]) > 1 else _Resp(500, text="OOM")
        return _Resp(200, {})

    a.session.post.side_effect = post
    payload = {"kind": "trellis_mesh", "image_b64": base64.b64encode(PNG).decode()}
    result, error = handlers.trellis_mesh(a, payload)

    assert error is None
    assert base64.b64decode(result["glb_b64"]) == GLB
    assert calls.count("http://trellis/generate") == 2


def test_trellis_handler_carries_the_servers_timing_split():
    """A pod's stdout is unreachable, so cold-start attribution only survives if the load /
    generate split rides back on the job row."""
    a = _agent(queue="mesh", target="http://trellis")
    a.session.get.side_effect = lambda url, **kw: _Resp(200, {"data": []})
    a.session.post.side_effect = lambda url, **kw: _Resp(
        200, content=GLB, headers={"X-Load-Seconds": "5.6", "X-Generate-Seconds": "16.2"})
    result, error = handlers.trellis_mesh(
        a, {"kind": "trellis_mesh", "image_b64": base64.b64encode(PNG).decode()})

    assert error is None
    assert result["load_seconds"] == "5.6"
    assert result["generate_seconds"] == "16.2"


def test_trellis_handler_gives_up_after_two_attempts():
    a = _agent(queue="mesh", target="http://trellis")
    a.session.get.side_effect = lambda url, **kw: _Resp(200, {"data": []})
    a.session.post.side_effect = lambda url, **kw: (
        _Resp(500, text="OOM") if url.endswith("/generate") else _Resp(200, {}))
    result, error = handlers.trellis_mesh(
        a, {"image_b64": base64.b64encode(PNG).decode()})
    assert result is None and "OOM" in error


def test_agent_dispatches_on_payload_kind(monkeypatch):
    a = _agent()
    monkeypatch.setitem(handlers.HANDLERS, "comfy_image",
                        lambda agent, payload: ({"images": []}, None))
    a.session.post.side_effect = lambda url, **kw: _Resp(200, {"ok": True})
    a.execute({"id": "j1", "payload": {"kind": "comfy_image", "workflow": {}}})
    a._drain_uploads()

    body = a.session.post.call_args_list[-1].kwargs["json"]
    assert body["job_id"] == "j1" and body["result"] == {"images": []}
    assert body["error"] is None
