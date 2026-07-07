from contextvars import ContextVar
from typing import Optional, Dict
from contextlib import contextmanager
from pathlib import Path

_task_id_var: ContextVar[Optional[str]] = ContextVar('task_id', default=None)
_subtask_id_var: ContextVar[Optional[str]] = ContextVar('subtask_id', default=None)
_working_directory_var: ContextVar[Optional[str]] = ContextVar('working_directory', default=None)
_user_id_var: ContextVar[Optional[str]] = ContextVar('user_id', default=None)


@contextmanager
def execution_context(task_id: Optional[str] = None, subtask_id: Optional[str] = None, working_directory: Optional[str] = None):
    # Restore prior VALUES instead of reset(token): this wraps async streaming generators that
    # yield across task/context boundaries, so __enter__ and the finally can run in different
    # Contexts — and ContextVar.reset() rejects a token created in another Context.
    prev = (_task_id_var.get(), _subtask_id_var.get(), _working_directory_var.get())
    _task_id_var.set(task_id)
    _subtask_id_var.set(subtask_id)
    _working_directory_var.set(working_directory)
    try:
        yield
    finally:
        _task_id_var.set(prev[0])
        _subtask_id_var.set(prev[1])
        _working_directory_var.set(prev[2])


@contextmanager
def user_id_scope(user_id: Optional[str]):
    """Bind the authenticated user for the duration of a call so run-creating tools
    (`create_run`) can attribute a new run to its owner. Restores the prior value (not
    reset(token)) because it wraps streaming generators that resume across Contexts."""
    prev = _user_id_var.get()
    _user_id_var.set(user_id)
    try:
        yield
    finally:
        _user_id_var.set(prev)


def get_user_id() -> Optional[str]:
    return _user_id_var.get()


def get_task_id() -> Optional[str]:
    return _task_id_var.get()


def get_subtask_id() -> Optional[str]:
    return _subtask_id_var.get()


def get_working_directory() -> Optional[str]:
    return _working_directory_var.get()


def get_execution_context() -> Dict[str, Optional[str]]:
    return {
        'task_id': get_task_id(),
        'subtask_id': get_subtask_id(),
        'working_directory': get_working_directory(),
    }


def resolve_base_path(input_path: Optional[str] = None) -> Path:
    context_wd = get_working_directory()
    if context_wd:
        base_dir = Path(context_wd).resolve()
    else:
        from config.settings_manager import settings_manager
        base_dir = Path(settings_manager.get_settings()['working_directory']).resolve()

    if input_path is None:
        return base_dir

    path = Path(input_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()
