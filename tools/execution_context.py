"""
Execution context for automatic task_id, subtask_id, and working_directory injection into tool calls.

This module provides thread-safe context variables that track the current execution
context (task_id, subtask_id, and working_directory). The tool_manager automatically injects these values
into tool calls that accept them as parameters, eliminating the need for manual passing.

Thread Safety:
    Uses contextvars.ContextVar for thread-safe context isolation across concurrent
    agent executions. Each thread/async task maintains its own independent context.

Usage:
    with execution_context(task_id="task-123", subtask_id="sub-456", working_directory="outputs"):
        # All tool calls within this context automatically receive task_id/subtask_id/working_directory
        tool_manager.useTool("spawn_task", ...)
        tool_manager.useTool("write_to_file", path="output.txt", content="data")

Integration:
    - MaestroAgent sets context before agent execution
    - MainAgent sets context for top-level task execution
    - ToolManager reads context for auto-injection during tool calls
    - File tools use working_directory from context for path resolution

Functions:
    execution_context: Context manager to set execution context
    get_task_id: Get current task_id from context
    get_subtask_id: Get current subtask_id from context
    get_working_directory: Get current working_directory from context
    get_execution_context: Get full context as dictionary
    resolve_task_id: Resolve task_id from parameter or context
    resolve_base_path: Unified path resolution for tools
"""

from contextvars import ContextVar
from typing import Optional, Dict
from contextlib import contextmanager
from pathlib import Path

# Thread-safe context variables
_task_id_var: ContextVar[Optional[str]] = ContextVar('task_id', default=None)
_subtask_id_var: ContextVar[Optional[str]] = ContextVar('subtask_id', default=None)
_working_directory_var: ContextVar[Optional[str]] = ContextVar('working_directory', default=None)


@contextmanager
def execution_context(task_id: Optional[str] = None, subtask_id: Optional[str] = None, working_directory: Optional[str] = None):
    """
    Context manager to set execution context for tool calls.

    Usage:
        with execution_context(task_id="task-123", subtask_id="sub-456", working_directory="outputs"):
            tool_manager.useTool("spawn_task", ...)

    Args:
        task_id: Current task ID
        subtask_id: Current subtask ID
        working_directory: Working directory for file operations
    """
    # Save previous tokens to restore later
    task_token = _task_id_var.set(task_id)
    subtask_token = _subtask_id_var.set(subtask_id)
    wd_token = _working_directory_var.set(working_directory)

    try:
        yield
    finally:
        # Restore previous context
        _task_id_var.reset(task_token)
        _subtask_id_var.reset(subtask_token)
        _working_directory_var.reset(wd_token)


def get_task_id() -> Optional[str]:
    """Get the current task_id from execution context"""
    return _task_id_var.get()


def get_subtask_id() -> Optional[str]:
    """Get the current subtask_id from execution context"""
    return _subtask_id_var.get()


def get_working_directory() -> Optional[str]:
    """Get the current working_directory from execution context"""
    return _working_directory_var.get()


def get_execution_context() -> Dict[str, Optional[str]]:
    """
    Get full execution context as a dictionary.

    Returns:
        Dict with task_id, subtask_id, and working_directory (values may be None)
    """
    return {
        'task_id': get_task_id(),
        'subtask_id': get_subtask_id(),
        'working_directory': get_working_directory()
    }


def resolve_task_id(task_id: Optional[str] = None) -> str:
    """Resolve task_id from the execution context when not explicitly provided.

    This encapsulates the common pattern used by tool functions that accept an
    optional task_id parameter and fall back to the execution context.

    Args:
        task_id: An explicitly provided task_id, or None to use context.

    Returns:
        The resolved task_id (str).

    Raises:
        ValueError: If task_id is None and no context is available.
    """
    if task_id is None:
        task_id = get_task_id()
        if task_id is None:
            raise ValueError("task_id must be provided or available in execution context")
    return task_id


def resolve_base_path(input_path: Optional[str] = None) -> Path:
    """
    Unified path resolution for all tools.
    
    Resolution priority:
    1. Absolute paths are returned unchanged
    2. If execution context working directory is set, resolve relative paths against it
    3. Otherwise resolve relative paths against configured working directory from settings

    Args:
        input_path: Path to resolve (if None returns just the base directory)

    Returns:
        Resolved absolute Path object
    """
    from pathlib import Path
    
    # First get base directory
    context_wd = get_working_directory()
    if context_wd:
        base_dir = Path(context_wd)
    else:
        # Fallback to configured working directory from settings
        from config.settings_manager import settings_manager
        base_dir = Path(settings_manager.get_settings()['working_directory'])
    
    # Resolve base directory to absolute path
    base_dir = base_dir.resolve()
    
    # If no input path provided, return just base directory
    if input_path is None:
        return base_dir
    
    path = Path(input_path).expanduser()
    
    # Absolute paths are used as-is
    if path.is_absolute():
        return path.resolve()
    
    # Relative paths are resolved against base directory
    return (base_dir / path).resolve()
