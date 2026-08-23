"""Pod creation: the preferred card is asked for ALONE, and the fallback list is a second ask."""

from unittest.mock import MagicMock

from scaler.autoscaler import Autoscaler
from scaler.policy import StartPod
from scaler.runpod_client import RunPodError

QCFG = {"template_id": "tpl1",
        "gpu_type_ids": ["NVIDIA GeForce RTX 5090", "NVIDIA RTX PRO 4500 Blackwell"],
        "idle_exit_seconds": 30}
RP = {"cp_url": "https://cp", "network_volume_id": "vol1"}


def _scaler():
    a = Autoscaler(MagicMock(), MagicMock(), lambda: {})
    return a, a._client


LLM = {"model": "qwen3.8_27b", "n_ctx": 65535}


def _start(a, qcfg=QCFG, llm=LLM, queue="llm"):
    a._execute(StartPod(queue), queue, qcfg, RP, "wtoken", llm, now=100.0)


def test_preferred_gpu_is_requested_alone():
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_count == 1
    assert client.create_pod.call_args.kwargs["gpu_type_ids"] == ["NVIDIA GeForce RTX 5090"]


def test_refused_preferred_widens_to_the_whole_list():
    a, client = _scaler()
    client.create_pod.side_effect = [RunPodError("no 5090 capacity"), {"id": "pod1"}]
    _start(a)
    assert [c.kwargs["gpu_type_ids"] for c in client.create_pod.call_args_list] == [
        ["NVIDIA GeForce RTX 5090"], QCFG["gpu_type_ids"]]


def test_a_single_configured_gpu_is_asked_for_once():
    a, client = _scaler()
    client.create_pod.side_effect = RunPodError("no capacity")
    _start(a, {**QCFG, "gpu_type_ids": ["NVIDIA GeForce RTX 5090"]})
    assert client.create_pod.call_count == 1   # no pointless second identical ask


def test_both_asks_refused_is_logged_not_raised():
    a, client = _scaler()
    client.create_pod.side_effect = RunPodError("no capacity")
    _start(a)   # a bad tick is skipped, never fatal
    assert client.create_pod.call_count == 2


def test_pod_env_carries_no_gpu_type():
    """The card is the WORKER's to report — an env-injected guess is what recorded 879 prod
    jobs as 5090 while the bill was RTX PRO 4500."""
    a, client = _scaler()
    _start(a)
    env = client.create_pod.call_args.kwargs["env"]
    assert "GPU_TYPE" not in env
    assert env["CP_URL"] == "https://cp" and env["WORKER_TOKEN"] == "wtoken"


def test_pod_env_carries_the_model_string_the_control_plane_will_send():
    """ninfer rejects any request whose model is not its --model-id, and the pod cannot read
    settings — so the alias has to arrive at create."""
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_args.kwargs["env"]["LLM_MODEL"] == "qwen3.8_27b"


def test_pod_env_carries_the_window_the_control_plane_budgets_against():
    """The engine preallocates its window and the control plane trims its input to the same
    number; neither can be right unless the pod hears it at create."""
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_args.kwargs["env"]["LLM_N_CTX"] == "65535"


def test_only_an_llm_pod_hears_about_the_model():
    """A ComfyUI pod has no engine to name it to — the queue owns its backend."""
    a, client = _scaler()
    _start(a, queue="image")
    env = client.create_pod.call_args.kwargs["env"]
    assert "LLM_MODEL" not in env and "LLM_N_CTX" not in env


def test_tick_takes_the_model_off_settings():
    a, client = _scaler()
    a._settings = lambda: {"llm": {"model": "qwen3.8_27b", "n_ctx": 65535},
                           "workqueue": {"token": "wtoken"},
                           "runpod": {"cp_url": "https://cp", "network_volume_id": "vol1",
                                      "queues": {"llm": QCFG}}}
    a._stats.queue_stats.return_value = MagicMock(pending=1, oldest_age_seconds=5.0)
    a._stats.live_workers.return_value = []
    a._stats.stale_workers.return_value = []
    a._stats.terminated_workers_with_pods.return_value = []
    client.list_pods.return_value = []
    a.tick()
    assert client.create_pod.call_args.kwargs["env"]["LLM_MODEL"] == "qwen3.8_27b"


def _busy(a, client, pods):
    """One live worker already on a pod, and a queue deep enough to ask for another."""
    from scaler.stats import QueueStats, WorkerInfo
    a._settings = lambda: {"llm": {"model": "m", "n_ctx": 65535}, "workqueue": {"token": "wtoken"},
                           "runpod": {**RP, "queues": {"llm": {**QCFG, "max_workers": 3}}}}
    a._stats.queue_stats.return_value = QueueStats(pending=50,
                                                   oldest_pending_age_seconds=5.0)
    a._stats.live_workers.return_value = [WorkerInfo("w0", "p0")]
    a._stats.stale_workers.return_value = []
    a._stats.terminated_workers_with_pods.return_value = []
    client.list_pods.side_effect = lambda: list(pods)


def _tick(a, monkeypatch, now):
    monkeypatch.setattr("scaler.autoscaler.time.time", lambda: now)
    a.tick()


def test_a_second_tick_inside_the_cooldown_starts_no_pod(monkeypatch):
    a, client = _scaler()
    pods = [{"id": "p0", "name": "maestro-llm-p0"}]
    _busy(a, client, pods)
    client.create_pod.return_value = {"id": "p1"}
    _tick(a, monkeypatch, 100.0)
    assert client.create_pod.call_count == 1
    pods.append({"id": "p1", "name": "maestro-llm-p1"})
    _tick(a, monkeypatch, 101.0)
    assert client.create_pod.call_count == 1
    _tick(a, monkeypatch, 100.0 + QCFG.get("cooldown_seconds", 90))
    assert client.create_pod.call_count == 2


def test_a_widened_create_starts_the_cooldown_too(monkeypatch):
    a, client = _scaler()
    pods = [{"id": "p0", "name": "maestro-llm-p0"}]
    _busy(a, client, pods)
    client.create_pod.side_effect = [RunPodError("no capacity"), {"id": "p1"}, {"id": "p2"}]
    _tick(a, monkeypatch, 100.0)
    assert client.create_pod.call_count == 2
    pods.append({"id": "p1", "name": "maestro-llm-p1"})
    _tick(a, monkeypatch, 101.0)
    assert client.create_pod.call_count == 2


def test_a_refused_create_is_retried_on_the_next_tick(monkeypatch):
    a, client = _scaler()
    _busy(a, client, [{"id": "p0", "name": "maestro-llm-p0"}])
    client.create_pod.side_effect = RunPodError("no capacity")
    _tick(a, monkeypatch, 100.0)
    assert client.create_pod.call_count == 2
    _tick(a, monkeypatch, 101.0)
    assert client.create_pod.call_count == 4


def test_cuda_floor_rides_the_widened_ask_too():
    """An engine that needs the driver needs it on every card: an old-driver host is a dead pod
    whatever it was asked for."""
    a, client = _scaler()
    qcfg = {**QCFG, "allowed_cuda_versions": ["13.0"]}
    client.create_pod.side_effect = [RunPodError("no 13.0 5090 host"), {"id": "pod1"}]
    _start(a, qcfg)
    head, widened = client.create_pod.call_args_list
    assert head.kwargs["allowed_cuda_versions"] == ["13.0"]
    assert widened.kwargs["allowed_cuda_versions"] == ["13.0"]


def test_a_queue_with_an_any_driver_fallback_drops_the_floor_on_widen():
    """The llm image carries llama.cpp beside ninfer; a slow pod beats no pod."""
    a, client = _scaler()
    qcfg = {**QCFG, "allowed_cuda_versions": ["13.0"], "fallback_drops_cuda_floor": True}
    client.create_pod.side_effect = [RunPodError("no 13.0 5090 host"), {"id": "pod1"}]
    _start(a, qcfg)
    head, widened = client.create_pod.call_args_list
    assert head.kwargs["allowed_cuda_versions"] == ["13.0"]
    assert widened.kwargs["allowed_cuda_versions"] is None


def test_no_cuda_floor_configured_sends_none():
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_args.kwargs["allowed_cuda_versions"] is None
