"""LoggingConnector — wrap a connector so every LLM call during a build is saved to disk for audit.

The build's LLM calls all funnel through `conn.generate_with_tools` (the agent loop via
`Services.infer`, and the per-scene `rewrite_node`). Wrapping the connector captures all of them in
one place: each call writes one numbered JSON file under `<run_dir>/llm_calls/` holding the full
request (messages + tool names + kwargs) and the raw response, so a finished run can be replayed
call-by-call. The wrapper delegates every other attribute to the inner connector, so call sites are
unchanged.
"""

import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class LoggingConnector:
    def __init__(self, inner, log_dir):
        self._inner = inner
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._n = 0
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def generate_with_tools(self, messages, schemas=None, **kwargs):
        with self._lock:
            self._n += 1
            seq = self._n
        t0 = time.perf_counter()
        try:
            response = self._inner.generate_with_tools(messages, schemas, **kwargs)
        except Exception as e:
            self._write(seq, messages, schemas, kwargs, {"exception": repr(e)},
                        time.perf_counter() - t0)
            raise
        self._write(seq, messages, schemas, kwargs, response, time.perf_counter() - t0)
        return response

    def _write(self, seq, messages, schemas, kwargs, response, elapsed) -> None:
        record = {
            "seq": seq,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_s": round(elapsed, 3),
            "request": {
                "messages": messages,
                "tool_names": [s.get("function", {}).get("name") for s in (schemas or [])],
                "kwargs": kwargs,
            },
            "response": response,
        }
        try:
            (self._log_dir / f"{seq:04d}.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            logger.exception("failed to write llm call log %d", seq)
