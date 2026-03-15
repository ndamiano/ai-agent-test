"""Execution context for passing task_id and subtask_id to tool calls automatically"""

from contextvars import ContextVar
from typing import Optional, Dict
from contextlib import contextmanager

# Thread-safe context variables
_task_id_var: ContextVar[Optional[str]] = ContextVar('task_id', default=None)
_subtask_id_var: ContextVar[Optional[str]] = ContextVar('subtask_id', default=None)


@contextmanager
def execution_context(task_id: Optional[str] = None, subtask_id: Optional[str] = None):
    """
    Context manager to set execution context for tool calls.

    Usage:
        with execution_context(task_id="task-123", subtask_id="sub-456"):
            tool_manager.useTool("spawn_task", ...)

    Args:
        task_id: Current task ID
        subtask_id: Current subtask ID
    """
    # Save previous tokens to restore later
    task_token = _task_id_var.set(task_id)
    subtask_token = _subtask_id_var.set(subtask_id)

    try:
        yield
    finally:
        # Restore previous context
        _task_id_var.reset(task_token)
        _subtask_id_var.reset(subtask_token)


def get_task_id() -> Optional[str]:
    """Get the current task_id from execution context"""
    return _task_id_var.get()


def get_subtask_id() -> Optional[str]:
    """Get the current subtask_id from execution context"""
    return _subtask_id_var.get()


def get_execution_context() -> Dict[str, Optional[str]]:
    """
    Get full execution context as a dictionary.

    Returns:
        Dict with task_id and subtask_id (values may be None)
    """
    return {
        'task_id': get_task_id(),
        'subtask_id': get_subtask_id()
    }


def has_execution_context() -> bool:
    """Check if we're running within an execution context"""
    return get_task_id() is not None


def require_task_id() -> str:
    """
    Get task_id from execution context, raising error if not available.

    Use this in tools that need task_id but don't want it as a parameter.

    Raises:
        RuntimeError: If no task_id in current execution context

    Returns:
        Current task_id
    """
    task_id = get_task_id()
    if task_id is None:
        raise RuntimeError(
            "This tool requires a task_id but none is available in execution context. "
            "Make sure this tool is called from within a task execution."
        )
    return task_id


def require_execution_context() -> Dict[str, str]:
    """
    Get full execution context, raising error if not available.

    Raises:
        RuntimeError: If no execution context set

    Returns:
        Dict with task_id and subtask_id
    """
    context = get_execution_context()
    if context['task_id'] is None:
        raise RuntimeError(
            "This tool requires execution context but none is available. "
            "Make sure this tool is called from within a task execution."
        )
    return context
