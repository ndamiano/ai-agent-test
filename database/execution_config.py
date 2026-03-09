"""Execution configuration management for task orchestration."""

import os
from dataclasses import dataclass
from typing import Optional

from .task_store import task_store


@dataclass
class ExecutionConfig:
    """Configuration for how a task should be executed."""
    mode: str  # "sequential" or "parallel"
    max_workers: int = 2  # Only relevant for parallel mode
    retry_failed: bool = True
    max_retries: int = 1


def get_execution_config(task_id: str) -> ExecutionConfig:
    """Get execution configuration for a task from the task store.
    
    Args:
        task_id: The ID of the task
        
    Returns:
        ExecutionConfig with appropriate settings based on task's execution_mode
    """
    task = task_store.get_task(task_id)
    execution_mode = task["execution_mode"]
    
    if execution_mode == "sequential":
        return ExecutionConfig(
            mode="sequential",
            max_workers=1,  # Always 1 for sequential
            retry_failed=True,
            max_retries=1
        )
    elif execution_mode == "parallel":
        max_parallel_workers = int(os.getenv("MAX_PARALLEL_WORKERS", "2"))
        return ExecutionConfig(
            mode="parallel",
            max_workers=max_parallel_workers,
            retry_failed=True,
            max_retries=1
        )
    else:
        raise ValueError(f"Unknown execution mode: {execution_mode}")


def execution_mode_from_goal(goal: str) -> str:
    """Make a heuristic guess at whether a task should be sequential or parallel.
    
    Args:
        goal: The task goal description
        
    Returns:
        "sequential" or "parallel" based on keywords in the goal
    """
    goal_lower = goal.lower()
    
    # Keywords that suggest sequential execution (strong ordering dependencies)
    sequential_keywords = [
        "book", "story", "write", "chapter", "novel", "script", "code", "program",
        "develop", "build", "create", "design", "compose", "draft", "outline"
    ]
    
    # Keywords that suggest parallel execution (independent work)
    parallel_keywords = [
        "research", "find", "search", "compare", "check", "analyze", "investigate",
        "gather", "collect", "review", "examine", "study", "explore", "identify"
    ]
    
    # Count matches for each category
    sequential_matches = sum(1 for keyword in sequential_keywords if keyword in goal_lower)
    parallel_matches = sum(1 for keyword in parallel_keywords if keyword in goal_lower)
    
    # If more sequential keywords, go sequential
    if sequential_matches > parallel_matches:
        return "sequential"
    # If more parallel keywords, go parallel
    elif parallel_matches > sequential_matches:
        return "parallel"
    # If equal or no matches, default to sequential (safer)
    else:
        return "sequential"


# Default execution mode from environment variable
DEFAULT_EXECUTION_MODE = os.getenv("DEFAULT_EXECUTION_MODE", "sequential")