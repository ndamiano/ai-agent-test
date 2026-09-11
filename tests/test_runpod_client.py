"""RunPod REST client: payload shape, bearer auth, idempotent terminate, loud non-2xx."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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
                       {"CP_URL": "https://cp", "WORKER_TOKEN": "t"}, allowed_cuda_versions=["12.8"])
    assert out == {"id": "pod1"}
    url = c.session.post.call_args.args[0]
    body = c.session.post.call_args.kwargs["json"]
    assert url == "https://api.runpod.io/v2/pods"
    assert body["gpu"] == {"id": "NVIDIA GeForce RTX 5090", "count": 1,
                           "allowedCudaVersions": ["12.8"]}
    assert body["templateId"] == "tpl1"
    assert body["mounts"] == {"network": [{"volumeId": "vol1", "path": "/workspace"}]}
    assert body["cloud"] == "SECURE"
    assert body["env"]["WORKER_TOKEN"] == "t"


def test_create_pod_asks_each_preferred_card_in_order_until_one_is_granted():
    c = _client()
    c.session.post.side_effect = [_resp(500, text="no instances currently available"),
                                  _resp(201, {"id": "pod1"})]
    assert c.create_pod("n", "tpl", ["a", "b", "c"], "vol", {}) == {"id": "pod1"}
    assert [k.kwargs["json"]["gpu"]["id"] for k in c.session.post.call_args_list] == ["a", "b"]


def test_create_pod_refused_on_every_card_raises_every_refusal():
    c = _client()
    c.session.post.side_effect = [_resp(500, text="no a"), _resp(500, text="no b")]
    with pytest.raises(RunPodError, match="no a.*no b"):
        c.create_pod("n", "tpl", ["a", "b"], "vol", {})


def test_list_pods_unwraps_the_envelope():
    c = _client()
    c.session.get.return_value = _resp(200, {"pods": [{"id": "p1"}]})
    assert c.list_pods() == [{"id": "p1"}]


def test_billing_pods_unwraps_the_records():
    c = _client()
    c.session.get.return_value = _resp(200, {"records": [{"podId": "p1"}], "metadata": {}})
    assert c.billing_pods("s", "e", bucket="hour") == [{"podId": "p1"}]
    assert c.session.get.call_args.kwargs["params"] == {
        "startTime": "s", "endTime": "e", "bucketSize": "hour"}


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
