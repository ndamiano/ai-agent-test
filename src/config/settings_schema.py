"""Pydantic schemas for settings validation"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# Reasoning-effort knob, sent as reasoning.effort when the worker speaks the Responses API.
# "none" disables reasoning — the lever that stops a local model spending ~30k tokens thinking per
# call. None = the model's own default. ("off" is NOT a valid value — it errors.)
ReasoningLevel = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class LLMSettings(BaseModel):
    model: str = Field(..., min_length=1)
    max_tokens: int = Field(50000, ge=1)
    n_ctx: int = Field(32768, ge=1, description=(
        "The server's runtime context window in tokens (llama-server `-c`). The local router does not "
        "report it, so set it to match your launch flag. MessageBuilder budgets input against this so "
        "it trims the transcript BEFORE the prompt overflows the window; too-large a value = no trim."))
    reasoning: Optional[ReasoningLevel] = None


class WorkQueueSettings(BaseModel):
    """The worker-pull inference queue: jobs rows executed by worker agents (worker/agent.py) that
    claim over HTTP — the same shape whether the worker is the local 5090 or a RunPod pod. It is
    the ONLY transport to a GPU (llm, image and mesh alike), so a worker per queue is mandatory,
    not an opt-in. The worker owns its backend address (`--target`); no endpoint lives here."""
    token: str = Field("", description=(
        "Shared bearer token worker agents present on /worker endpoints. Empty = every worker "
        "request is refused, even when enabled."))
    job_timeout_seconds: int = Field(900, ge=1, description=(
        "How long an enqueuer waits for a claimed job to complete before giving up."))
    lease_seconds: int = Field(120, ge=5, description=(
        "A claimed job returns to pending if the worker misses heartbeats for this long."))


class RunPodQueueScaling(BaseModel):
    """Per-queue autoscaling policy. Workers own scale-DOWN (idle self-exit via the claim
    long-poll window); the control plane owns scale-UP + pod reaping (scaler/)."""
    template_id: str = Field(..., min_length=1)
    gpu_type_ids: List[str] = Field(..., min_length=1)
    max_workers: int = Field(2, ge=1)
    scale_up_depth_per_worker: int = Field(10, ge=1, description=(
        "Add a pod when pending jobs ÷ effective workers (live + still booting) reaches this."))
    scale_up_max_age_seconds: int = Field(300, ge=1, description=(
        "Also add a pod when the oldest pending job has waited this long — catches every worker "
        "busy on long jobs with queue depth under the threshold."))
    cooldown_seconds: int = Field(90, ge=0)
    idle_exit_seconds: int = Field(10, ge=0, description=(
        "Delivered to pods as IDLE_EXIT_SECONDS: the worker's claim long-poll window, after "
        "which a null claim means exit. 0 = never exit (the home-box default)."))
    boot_deadline_seconds: int = Field(900, ge=1, description=(
        "A pod this old with no worker row counts as wedged and is reaped; younger, it counts "
        "as starting capacity so scale-up can't add-forever during a boot."))


class RunPodSettings(BaseModel):
    enabled: bool = Field(False)
    api_key: str = Field("")
    network_volume_id: str = Field("")
    cloud_type: str = Field("SECURE")
    cp_url: str = Field("", description=(
        "The control-plane base URL delivered to pods as CP_URL — must be reachable from "
        "RunPod's network, so a public/tailnet-funnel URL, never localhost."))
    tick_seconds: int = Field(15, ge=1)
    stale_worker_seconds: int = Field(180, ge=1, description=(
        "A pod-backed worker unseen this long is presumed dead: its row is terminated and its "
        "pod reaped. Must exceed the claim window + heartbeat interval with margin."))
    queues: Dict[str, RunPodQueueScaling] = Field(default_factory=dict)


class ModelCategorySettings(BaseModel):
    message_budget_chars: int = Field(30000, ge=1000)


DEFAULT_MODEL_CATEGORIES: Dict[str, ModelCategorySettings] = {
    "large":  ModelCategorySettings(message_budget_chars=30000),
    "medium": ModelCategorySettings(message_budget_chars=20000),
    "small":  ModelCategorySettings(message_budget_chars=250000),
}


class AppSettings(BaseModel):
    working_directory: Optional[str] = "outputs"
    # Control-plane state (platform.db + auth.db) — NOT under working_directory: artifact output
    # is the wrong home for the datastore.
    data_dir: Optional[str] = "data"
    model_category: Literal["large", "medium", "small"] = "large"
    llm: LLMSettings
    workqueue: Optional[WorkQueueSettings] = None
    runpod: Optional[RunPodSettings] = None
