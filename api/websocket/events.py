"""WebSocket event builders for standardized event construction"""

from datetime import datetime
from typing import Dict, Any


def create_task_event(event_type: str, task_id: str, **kwargs) -> Dict[str, Any]:
    """
    Create a standardized task event for broadcasting.

    Args:
        event_type: Type of event (e.g., 'task_created', 'task_completed')
        task_id: ID of the task
        **kwargs: Additional event-specific fields

    Returns:
        Dictionary with event data including timestamp
    """
    return {
        'type': event_type,
        'task_id': task_id,
        'timestamp': datetime.now().isoformat(),
        **kwargs
    }


def create_subtask_event(
    event_type: str,
    task_id: str,
    subtask_id: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a standardized subtask event for broadcasting.

    Args:
        event_type: Type of event (e.g., 'subtask_started', 'subtask_completed')
        task_id: ID of the task
        subtask_id: ID of the subtask
        **kwargs: Additional event-specific fields

    Returns:
        Dictionary with event data including timestamp
    """
    return {
        'type': event_type,
        'task_id': task_id,
        'subtask_id': subtask_id,
        'timestamp': datetime.now().isoformat(),
        **kwargs
    }
