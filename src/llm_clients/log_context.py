"""Per-run LLM log directory context variable."""

from contextvars import ContextVar

_log_dir: ContextVar[str | None] = ContextVar("llm_log_dir", default=None)


def set_log_dir(path: str):
    return _log_dir.set(path)


def reset_log_dir(token) -> None:
    _log_dir.reset(token)


def get_log_dir() -> str | None:
    return _log_dir.get()
