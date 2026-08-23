from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from config.settings_manager import settings_manager

_run_id_var: ContextVar[Optional[str]] = ContextVar('run_id', default=None)


@contextmanager
def run_scope(run_id: Optional[str]):
    """Bind the run whose build is executing so inference enqueued anywhere below (the queue
    connector) is attributed — and metered — to that game. Restores the prior VALUE instead of
    reset(token): this wraps generators that resume across Contexts, and ContextVar.reset()
    rejects a token created in another Context."""
    prev = _run_id_var.get()
    _run_id_var.set(run_id)
    try:
        yield
    finally:
        _run_id_var.set(prev)


def get_run_id() -> Optional[str]:
    return _run_id_var.get()


def resolve_base_path(input_path: Optional[str] = None) -> Path:
    base_dir = Path(settings_manager.get_settings()['working_directory']).resolve()
    if input_path is None:
        return base_dir

    path = Path(input_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()
