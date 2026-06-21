"""Per-run LLM log directory context variable."""

from contextvars import ContextVar

_log_dir: ContextVar[str | None] = ContextVar("llm_log_dir", default=None)


def get_log_dir() -> str | None:
    return _log_dir.get()
