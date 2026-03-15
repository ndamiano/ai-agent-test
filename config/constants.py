"""Application constants"""

# Task statuses
TASK_STATUSES = [
    'pending',
    'planning',
    'in_progress',
    'completed',
    'failed',
    'cancelled',
    'needs_assistance',
    'archived'
]

# Subtask statuses
SUBTASK_STATUSES = [
    'pending',
    'in_progress',
    'completed',
    'failed'
]

# Event types
EVENT_TYPES = [
    'task_created',
    'task_planned',
    'subtask_started',
    'subtask_completed',
    'subtask_failed',
    'task_completed',
    'task_failed',
    'agent_message',
    'tool_usage'
]
