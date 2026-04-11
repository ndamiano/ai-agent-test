"""Database constants for statuses and event types."""

# Task statuses
TASK_STATUSES = {
    "pending",
    "refining",
    "synthesizing",
    "planning",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
    "archived",
}

SUBTASK_STATUSES = {
    "pending",
    "in_progress",
    "completed",
    "failed",
}
