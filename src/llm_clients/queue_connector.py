"""Queue transport for LLM inference.

Same request building and response normalization as OpenAICompatibleConnector — only the
transport differs: instead of POSTing the Responses payload to a local server, it lands the
payload as a jobs row and waits for a worker agent to claim, execute, and complete it. The
worker forwards the payload verbatim to ITS local server, so model routing/eviction live with
the GPU, not here. Callers see the identical chat-shaped {choices, usage} result.
"""

import logging
import time
import uuid
from typing import Optional

from llm_clients.openai_compatible_connector import (
    OpenAICompatibleConnector, _responses_to_chat, _log_call, _REASONING_UNSET,
)

logger = logging.getLogger(__name__)


class QueueConnector(OpenAICompatibleConnector):
    connector_name = "queue"

    def __init__(self, *args, queue: str = "llm", job_timeout_seconds: float = 900, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = queue
        self.job_timeout_seconds = job_timeout_seconds

    def get_context_length(self) -> Optional[int]:
        """No base_url to ask — the configured server window (lmstudio.n_ctx) is the answer."""
        if self._context_length is None:
            from config.settings_manager import settings_manager
            n_ctx = (settings_manager.get_settings().get("lmstudio") or {}).get("n_ctx")
            self._context_length = int(n_ctx) if n_ctx else None
        return self._context_length

    def _call_responses(self, messages: list, tools: list, response_format,
                        max_tokens, request_id: str, reasoning=_REASONING_UNSET,
                        model: str = None, temperature: float = None) -> dict:
        payload = self._responses_payload(messages, tools, response_format, max_tokens,
                                          stream=False, reasoning=reasoning, model=model,
                                          temperature=temperature)
        t0 = time.perf_counter()
        job = self._run_job(payload)
        if job["status"] == "done":
            result = _responses_to_chat(job["result"])
            _log_call(payload["model"], len(messages), bool(tools),
                      time.perf_counter() - t0, result)
            self._log_llm(messages, result)
            return result
        # Worker reported an upstream error. A server that rejects text.format degrades to
        # free-form rather than failing the whole call, same as the direct transport.
        error = job.get("error") or "job lost"
        if response_format and "format" in error:
            logger.warning("Endpoint does not support text.format, retrying without it")
            self._response_format_supported = False
            payload.pop("text", None)
            job = self._run_job(payload)
            if job["status"] == "done":
                result = _responses_to_chat(job["result"])
                self._log_llm(messages, result)
                return result
            error = job.get("error") or "job lost"
        self._log_llm(messages, None, error)
        return {"error": error}

    def _run_job(self, payload: dict) -> dict:
        """Enqueue one Responses request and wait for a worker to land it. Returns the job row;
        status 'failed' with an error on timeout, so callers have one shape to branch on."""
        from db import queue_client

        return queue_client.run_job(
            self.queue, {"path": "/v1/responses", "body": payload},
            model=payload.get("model"), timeout_seconds=self.job_timeout_seconds)

    def generate_with_tools_stream(self, messages: list, tools: list = None):
        """The queue is request/response; streaming callers get the full reply as one chunk."""
        result = self.generate_with_tools(messages, tools)
        if "error" in result:
            yield {"error": result["error"]}
            return
        msg = result["choices"][0]["message"]
        yield {"id": f"queue-{uuid.uuid4().hex[:8]}", "choices": []}
        if msg.get("content"):
            yield {"choices": [{"index": 0, "delta": {"content": msg["content"]}}]}
        for i, tc in enumerate(msg.get("tool_calls") or []):
            yield {"choices": [{"index": 0, "delta": {"tool_calls": [{**tc, "index": i}]}}]}
        finish = "tool_calls" if msg.get("tool_calls") else "stop"
        yield {"usage": result.get("usage", {}),
               "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
