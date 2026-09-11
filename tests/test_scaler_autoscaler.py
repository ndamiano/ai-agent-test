"""Pod creation: the preferred card is asked for ALONE, and the fallback list is a second ask."""

from unittest.mock import ANY, MagicMock

from scaler.autoscaler import Autoscaler, refusal_kind
from scaler.policy import StartPod
from scaler.stats import WorkerInfo
from scaler.runpod_client import RunPodError

QCFG = {"template_id": "tpl1",
        "gpu_type_ids": ["NVIDIA GeForce RTX 5090", "NVIDIA RTX PRO 4500 Blackwell"],
        "idle_exit_seconds": 30}
RP = {"cp_url": "https://cp", "network_volume_id": "vol1"}


def _scaler():
    a = Autoscaler(MagicMock(), MagicMock(), lambda: {})
    a._stats.booting_workers.return_value = []
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
    """The engine rejects any request whose model is not its served name, and the pod cannot
    read settings — so the string has to arrive at create."""
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_args.kwargs["env"]["LLM_MODEL"] == "qwen3.8_27b"


def test_pod_env_carries_the_window_the_control_plane_budgets_against():
    """The engine preallocates its window and the control plane trims its input to the same
    number; neither can be right unless the pod hears it at create."""
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_args.kwargs["env"]["LLM_N_CTX"] == "65535"


def test_pod_env_carries_the_engine_flags_so_tuning_is_never_a_new_image():
    """Graph batch sizes, draft tokens and the like are launch flags of the engine; the pod hears
    the control plane's string at create and an unset one is an empty list, not a missing
    variable. The local leg's ninfer flags are another engine's and stay home."""
    a, client = _scaler()
    _start(a, llm={**LLM, "sglang_args": "--cuda-graph-bs 1", "ninfer_args": "--kv-dtype int8"})
    env = client.create_pod.call_args.kwargs["env"]
    assert env["SGLANG_ARGS_EXTRA"] == "--cuda-graph-bs 1" and "NINFER_ARGS" not in env
    _start(a)
    assert client.create_pod.call_args.kwargs["env"]["SGLANG_ARGS_EXTRA"] == ""


def test_llm_pods_are_told_their_slot_count():
    a, client = _scaler()
    _start(a, llm={**LLM, "slots": 2})
    assert client.create_pod.call_args.kwargs["env"]["WORKER_SLOTS"] == "2"
    _start(a)
    assert client.create_pod.call_args.kwargs["env"]["WORKER_SLOTS"] == "1"


def test_a_queue_with_its_own_volumes_never_touches_the_shared_one():
    """The volume pins the datacenter, and the llm weights live in another one than the art."""
    a, client = _scaler()
    _start(a, {**QCFG, "network_volume_ids": ["vol-llm"]})
    assert client.create_pod.call_args.kwargs["network_volume_id"] == "vol-llm"
    _start(a)
    assert client.create_pod.call_args.kwargs["network_volume_id"] == "vol1"


def test_the_next_volume_is_asked_only_after_a_datacenter_refused_every_card():
    """Two volumes are two datacenters; the preferred card is worth the whole first datacenter
    before the second is tried at all."""
    a, client = _scaler()
    client.create_pod.side_effect = [RunPodError("no WK"), RunPodError("no SE either"),
                                     {"id": "pod1"}]
    _start(a, {**QCFG, "network_volume_ids": ["vol-eu", "vol-us"]})
    calls = [(c.kwargs["network_volume_id"], c.kwargs["gpu_type_ids"])
             for c in client.create_pod.call_args_list]
    assert calls == [("vol-eu", ["NVIDIA GeForce RTX 5090"]),
                     ("vol-eu", QCFG["gpu_type_ids"]),
                     ("vol-us", ["NVIDIA GeForce RTX 5090"])]


def test_every_volume_refused_is_logged_not_raised():
    a, client = _scaler()
    client.create_pod.side_effect = RunPodError("no capacity")
    _start(a, {**QCFG, "network_volume_ids": ["vol-eu", "vol-us"]})
    assert client.create_pod.call_count == 4


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
    a._stats.queue_stats.return_value = QueueStats(pending=50, job_seconds=30.0)
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


def test_no_cuda_floor_configured_sends_none():
    a, client = _scaler()
    _start(a)
    assert client.create_pod.call_args.kwargs["allowed_cuda_versions"] is None


def test_a_successful_create_writes_the_worker_row_with_the_pods_price():
    a, client = _scaler()
    client.create_pod.return_value = {"id": "pod1", "cost": 1.89,
                                      "gpu": {"id": "NVIDIA RTX PRO 6000 Blackwell", "count": 1}}
    _start(a)
    worker_id = a._stats.record_worker_created.call_args.args[0]
    a._stats.record_worker_created.assert_called_once_with(
        worker_id, "pod1", "llm", "NVIDIA RTX PRO 6000 Blackwell", 1.89)
    assert client.create_pod.call_args.kwargs["args"] == f"--worker-id {worker_id}"


def test_a_create_that_states_no_price_leaves_the_row_unpriced():
    a, client = _scaler()
    client.create_pod.return_value = {"id": "pod1"}
    _start(a)
    a._stats.record_worker_created.assert_called_once_with(ANY, "pod1", "llm", None, None)


def test_a_refused_create_writes_no_row():
    a, client = _scaler()
    client.create_pod.side_effect = RunPodError("no capacity")
    _start(a)
    a._stats.record_worker_created.assert_not_called()


def test_a_booting_pod_is_aged_from_its_create_not_the_tick_that_first_saw_it(monkeypatch):
    """A control-plane restart forgets first-seen; the row's started_at does not."""
    from scaler.stats import BootingInfo
    a, client = _scaler()
    _busy(a, client, [{"id": "p1", "name": "maestro-llm-p1"}])
    a._stats.booting_workers.return_value = [BootingInfo("p1", started_at=100.0)]
    _tick(a, monkeypatch, 1100.0)
    client.terminate_pod.assert_called_once_with("p1")
    a._stats.mark_pod_terminated.assert_called_once_with("p1")


def test_a_reaped_pod_is_marked_terminated_by_pod_id():
    from scaler.policy import TerminatePod
    a, client = _scaler()
    a._execute(TerminatePod("p1", "worker stale", worker_id="w1"), "llm", QCFG, RP, "t", LLM, 0.0)
    client.terminate_pod.assert_called_once_with("p1")
    a._stats.mark_pod_terminated.assert_called_once_with("p1")


STOCK_CARD = RunPodError("POST /v2/pods -> 500: create pod: There are no instances currently available")
STOCK_DC = RunPodError("POST /v2/pods -> 500: create pod: could not find any pods with required specifications")


def test_every_combo_refused_for_stock_is_recorded_once_as_a_stock_out():
    a, client = _scaler()
    client.create_pod.side_effect = [STOCK_CARD, STOCK_DC]
    _start(a)
    a._stats.record_pod_refusal.assert_called_once()
    queue, kind, attempts, error = a._stats.record_pod_refusal.call_args.args
    assert (queue, kind) == ("llm", "stock")
    assert [(t["volume"], t["gpu_type_ids"]) for t in attempts] == [
        ("vol1", ["NVIDIA GeForce RTX 5090"]), ("vol1", QCFG["gpu_type_ids"])]
    assert "could not find any pods" in attempts[1]["error"] and error == str(STOCK_DC)


def test_a_refusal_that_is_not_stock_is_recorded_under_its_own_kind():
    a, client = _scaler()
    client.create_pod.side_effect = [STOCK_CARD, RunPodError("POST /v2/pods -> 401: unauthorized")]
    _start(a)
    assert a._stats.record_pod_refusal.call_args.args[1] == "other"


def test_a_create_that_eventually_lands_records_nothing():
    a, client = _scaler()
    client.create_pod.side_effect = [STOCK_CARD, {"id": "pod1"}]
    _start(a)
    a._stats.record_pod_refusal.assert_not_called()


def test_a_landed_create_counts_as_a_request_for_the_rollup():
    a, client = _scaler()
    client.create_pod.side_effect = [STOCK_CARD, {"id": "pod1"}]
    _start(a)
    a._stats.record_pod_created.assert_called_once_with("llm")


def test_a_refused_create_is_not_also_counted_as_created():
    a, client = _scaler()
    client.create_pod.side_effect = [STOCK_CARD, STOCK_DC]
    _start(a)
    a._stats.record_pod_created.assert_not_called()


def test_v2_stock_wording_is_a_stock_out():
    v2 = ('POST https://api.runpod.io/v2/pods -> 400: {"detail":"There are no longer any instances '
          'available with the requested specifications. Please refresh and try again.",'
          '"status":400,"title":"Bad Request"}')
    assert refusal_kind([v2]) == "stock"
