from contextvars import ContextVar
from typing import Optional, Dict
from contextlib import contextmanager
from pathlib import Path

_task_id_var: ContextVar[Optional[str]] = ContextVar('task_id', default=None)
_subtask_id_var: ContextVar[Optional[str]] = ContextVar('subtask_id', default=None)
_working_directory_var: ContextVar[Optional[str]] = ContextVar('working_directory', default=None)


@contextmanager
def execution_context(task_id: Optional[str] = None, subtask_id: Optional[str] = None, working_directory: Optional[str] = None):
    task_token = _task_id_var.set(task_id)
    subtask_token = _subtask_id_var.set(subtask_id)
    wd_token = _working_directory_var.set(working_directory)
    try:
        yield
    finally:
        _task_id_var.reset(task_token)
        _subtask_id_var.reset(subtask_token)
        _working_directory_var.reset(wd_token)


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
