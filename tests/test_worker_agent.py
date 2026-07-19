"""Worker agent — claims, forwards the payload to the local target without leaking the worker
token, and lands result/error with measured exec time."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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
    a.execute({"id": "j1", "payload": {"path": "/v1/responses", "body": {"model": "m"}}})

    (gpu_url, gpu_kw) = calls[0]
    assert gpu_url == "http://gpu/v1/responses"
    assert gpu_kw["json"] == {"model": "m"}
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

    body = a.session.post.call_args_list[-1].kwargs["json"]
    assert body["result"] is None
    assert "failed to load" in body["error"]
