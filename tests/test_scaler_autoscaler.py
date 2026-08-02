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


def _start(a, qcfg=QCFG, llm_model="Qwen3.6-27B-UD-Q4_K_XL", queue="llm"):
    a._execute(StartPod(queue), queue, qcfg, RP, "wtoken", llm_model, now=100.0)


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
    assert client.create_pod.call_args.kwargs["env"]["LLM_MODEL"] == "Qwen3.6-27B-UD-Q4_K_XL"


def test_only_an_llm_pod_hears_about_the_model():
    """A ComfyUI pod has no engine to name it to — the queue owns its backend."""
    a, client = _scaler()
    _start(a, queue="image")
    assert "LLM_MODEL" not in client.create_pod.call_args.kwargs["env"]


def test_tick_takes_the_model_off_settings():
    a, client = _scaler()
    a._settings = lambda: {"llm": {"model": "Qwen3.6-27B-UD-Q4_K_XL"},
                           "workqueue": {"token": "wtoken"},
                           "runpod": {"cp_url": "https://cp", "network_volume_id": "vol1",
                                      "queues": {"llm": QCFG}}}
    a._stats.queue_stats.return_value = MagicMock(pending=1, claimed=0, oldest_age_seconds=5.0)
    a._stats.live_workers.return_value = []
    a._stats.stale_workers.return_value = []
    a._stats.terminated_workers_with_pods.return_value = []
    client.list_pods.return_value = []
    a.tick()
    assert client.create_pod.call_args.kwargs["env"]["LLM_MODEL"] == "Qwen3.6-27B-UD-Q4_K_XL"


def test_scale_up_cooldown_is_stamped_on_success():
    a, _ = _scaler()
    _start(a)
    assert a._last_scale_up["llm"] == 100.0


def test_cooldown_is_stamped_when_the_widened_create_lands():
    a, client = _scaler()
    client.create_pod.side_effect = [RunPodError("no capacity"), {"id": "p"}]
    _start(a)
    assert a._last_scale_up["llm"] == 100.0   # a widened create still counts as one scale-up


def test_no_cooldown_when_no_pod_was_created():
    a, client = _scaler()
    client.create_pod.side_effect = RunPodError("no capacity")
    _start(a)
    assert "llm" not in a._last_scale_up   # nothing started, so the next tick may try again


def test_cuda_floor_rides_the_head_ask_and_drops_on_widen():
    """The floor exists so the preferred card lands where its engine runs; the fallback cards
    run on any driver, so requiring it there would just shrink the pool."""
    a, client = _scaler()
    qcfg = {**QCFG, "allowed_cuda_versions": ["13.0"]}
    client.create_pod.side_effect = [RunPodError("no 13.0 5090 host"), {"id": "pod1"}]
    _start(a, qcfg)
    head, widened = client.create_pod.call_args_list
    assert head.kwargs["allowed_cuda_versions"] == ["13.0"]
    assert widened.kwargs["allowed_cuda_versions"] is None


def test_no_cuda_floor_configured_sends_none():
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_args.kwargs["allowed_cuda_versions"] is None
