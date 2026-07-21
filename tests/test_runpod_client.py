"""RunPod REST client: payload shape, bearer auth, idempotent terminate, loud non-2xx."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scaler.runpod_client import RunPodClient, RunPodError


def _resp(status=200, body=None, text=""):
    return SimpleNamespace(
        ok=status < 400, status_code=status, text=text, json=lambda: body,
        request=SimpleNamespace(method="X", url="http://api"))


def _client():
    c = RunPodClient("rp-key")
    c.session = MagicMock()
    return c


def test_bearer_header_is_set_from_the_api_key():
    assert RunPodClient("rp-key").session.headers["Authorization"] == "Bearer rp-key"


def test_create_pod_payload_shape():
    c = _client()
    c.session.post.return_value = _resp(201, {"id": "pod1"})
    out = c.create_pod("maestro-mesh-a1", "tpl1", ["NVIDIA GeForce RTX 5090"], "vol1",
                       {"CP_URL": "https://cp", "WORKER_TOKEN": "t"})
    assert out == {"id": "pod1"}
    url = c.session.post.call_args.args[0]
    body = c.session.post.call_args.kwargs["json"]
    assert url.endswith("/pods")
    assert body["gpuTypeIds"] == ["NVIDIA GeForce RTX 5090"]   # a LIST, not a scalar
    assert body["templateId"] == "tpl1"
    assert body["networkVolumeId"] == "vol1"
    assert body["cloudType"] == "SECURE"
    assert body["gpuCount"] == 1
    assert body["env"]["WORKER_TOKEN"] == "t"


def test_list_pods_returns_the_json():
    c = _client()
    c.session.get.return_value = _resp(200, [{"id": "p1"}])
    assert c.list_pods() == [{"id": "p1"}]


def test_terminate_tolerates_404():
    c = _client()
    c.session.delete.return_value = _resp(404, text="not found")
    c.terminate_pod("gone")   # no raise: already-terminated is the desired outcome


def test_non_2xx_raises_runpod_error():
    c = _client()
    c.session.post.return_value = _resp(401, text="unauthorized")
    with pytest.raises(RunPodError, match="401"):
        c.create_pod("n", "tpl", ["gpu"], "vol", {})
    c.session.delete.return_value = _resp(500, text="boom")
    with pytest.raises(RunPodError, match="500"):
        c.terminate_pod("p1")
