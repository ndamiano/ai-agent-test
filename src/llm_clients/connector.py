"""LLM inference: chat-shaped calls in, chat-shaped results out, executed by a worker agent.

Callers speak the OpenAI chat shape and so does the queue: the payload this builds is CANONICAL, and
the worker translates it for whatever its own target serves (see `llm_clients/wire.py`). Nothing here
knows or cares which engine is behind a queue — adding one is a worker change.
"""

import logging
import time
from typing import Optional

from config.settings_manager import settings_manager
from llm_clients.rate_limiter import get_llm_rate_limiter
from workqueue import client as queue_client

logger = logging.getLogger(__name__)


def _log_call(model: str, n_msgs: int, has_tools: bool, dt: float, result) -> None:
    """One watchable INFO line per LLM call — so a 100-step run isn't a silent black box."""
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    logger.info("llm call: model=%s msgs=%d tools=%s %.1fs out_tok=%s",
                model, n_msgs, has_tools, dt, usage.get("completion_tokens", "?"))


# on/off are what people reach for; map them onto the effort enum so a setting of "off" disables
# reasoning instead of being silently dropped (dropped = no field = unbounded reasoning again).
_REASONING_ALIASES = {"on": "high", "off": "none"}

# Sentinel: caller did not pass a per-call reasoning override, so fall back to self.reasoning.
_REASONING_UNSET = object()


def _resolve_effort(value):
    if isinstance(value, str):
        value = _REASONING_ALIASES.get(value, value)
    return value


class LLMConnector:
    def __init__(self, model: str = "default", max_tokens: Optional[int] = 50000,
                 reasoning: Optional[str] = None,
                 queue: str = "llm", job_timeout_seconds: float = 900):
        self.model_name = model
        self.max_tokens = max_tokens
        # Reasoning-effort for reasoning models, sent as reasoning.effort. "none" disables
        # reasoning; None lets the model pick. on/off aliased to the effort enum.
        self.reasoning = _resolve_effort(reasoning)
        self.queue = queue
        self.job_timeout_seconds = job_timeout_seconds
        self._context_length: Optional[int] = None

    def get_context_length(self) -> Optional[int]:
        """No server to ask — the configured window (llm.n_ctx) is the answer."""
        if self._context_length is None:
            n_ctx = (settings_manager.get_settings().get("llm") or {}).get("n_ctx")
            self._context_length = int(n_ctx) if n_ctx else None
        return self._context_length

    def generate_with_tools(self, messages: list, tools: list = None, max_tokens: int = None,
                            reasoning=_REASONING_UNSET, temperature: float = None) -> dict:
        rate_limiter = get_llm_rate_limiter()
        if not rate_limiter.acquire(blocking=True, timeout=10):
            error_msg = "Rate limit exceeded: too many LLM requests"
            logger.warning(error_msg)
            return {"error": error_msg}

        payload = self._payload(messages, tools, max_tokens, reasoning=reasoning,
                                temperature=temperature)
        t0 = time.perf_counter()
        job = self._run_job(payload)
        if job["status"] == "done":
            result = job["result"] or {}
            _log_call(payload["model"], len(messages), bool(tools),
                      time.perf_counter() - t0, result)
            return result
        error = job.get("error") or "job lost"
        logger.error("LLM call (%s) failed: %s", self.model_name, error)
        return {"error": error}

    def _payload(self, messages: list, tools: list, max_tokens, reasoning=_REASONING_UNSET,
                 temperature: float = None) -> dict:
        """The CANONICAL request body — OpenAI chat shape, plus `reasoning` as a plain effort string.

        Sampling is the SERVER's: penalties and template switches are launch flags, so a default
        here would silently override whatever the operator chose."""
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.7 if temperature is None else temperature,
        }
        # An unset cap is sent as no cap at all: a number here is subtracted from the window the
        # INPUT may use, so a caller whose prompt is the long half must be able to decline one.
        cap = max_tokens if max_tokens is not None else self.max_tokens
        if cap is not None:
            payload["max_tokens"] = cap
        effort = self.reasoning if reasoning is _REASONING_UNSET else _resolve_effort(reasoning)
        if effort:
            payload["reasoning"] = effort
        if tools:
            payload["tools"] = tools
        return payload

    def _run_job(self, payload: dict) -> dict:
        """Enqueue one request and wait for a worker to land it. Returns the job row; status
        'failed' with an error on timeout, so callers have one shape to branch on."""
        return queue_client.run_job(
            self.queue, {"kind": "llm", "body": payload},
            model=payload.get("model"), timeout_seconds=self.job_timeout_seconds)

    def build_llm_job(self, messages: list, tools: list = None, max_tokens: int = None,
                      reasoning=_REASONING_UNSET) -> tuple:
        """Build the (queue payload, model) for one request WITHOUT enqueuing or waiting — the
        fire-and-forget seam the build chain uses: it lands the job itself (with build metadata) and
        drives the next turn from the completion, rather than blocking on run_job."""
        body = self._payload(messages, tools, max_tokens, reasoning=reasoning)
        return {"kind": "llm", "body": body}, body.get("model")


_cached_connector: Optional[LLMConnector] = None


def get_connector() -> LLMConnector:
    global _cached_connector
    if _cached_connector is None:
        settings = settings_manager.get_settings()
        llm = settings.get("llm") or {}
        workqueue = settings.get("workqueue") or {}
        _cached_connector = LLMConnector(
            model=llm.get("model", "default"),
            max_tokens=llm.get("max_tokens", 50000),
            reasoning=llm.get("reasoning"),
            job_timeout_seconds=workqueue.get("job_timeout_seconds", 900))
    return _cached_connector
