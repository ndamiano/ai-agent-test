import asyncio
import logging
from fastapi import APIRouter, HTTPException, status, Query, Request
from typing import List, Optional
from api.models.requests import CreateTaskRequest
from api.models.responses import TaskResponse, TaskDetailResponse, SubtaskResponse, EventResponse
from agents.maestro_agent import MaestroAgent
from database.task_store import task_store
from api.websocket.event_bus import event_bus
from config.time_utils import format_relative_time, get_utc_timestamp

logger = logging.getLogger(__name__)

maestro = MaestroAgent()

router = APIRouter()

def _task_to_response(task: dict) -> TaskResponse:
    """Convert a task dict to a TaskResponse with relative time."""
    return TaskResponse(
        **task,
        created_at_relative=format_relative_time(task["created_at"])
    )


@router.post("/", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(request: Request, task_request: CreateTaskRequest):
    """
    Create and run a task in the background.
    Returns immediately with task details while the task runs asynchronously.
    """
    task_dict = await asyncio.to_thread(
        task_store.create_task,
        task_request.goal,
        task_request.execution_mode or "sequential",
        task_request.working_directory
    )
    task_id = task_dict["id"]
    logger.info(f"Task created (background): {task_id}")

    await event_bus.publish({
        'type': 'task_created',
        'task_id': task_id,
        'goal': task_request.goal,
        'timestamp': get_utc_timestamp()
    })

    await asyncio.to_thread(maestro.run_background, task_id)

    task = await asyncio.to_thread(task_store.get_task, task_id)
    return _task_to_response(task)

@router.get("/", response_model=List[TaskResponse])
async def get_tasks(
    request: Request,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """
    List all tasks, optionally filtered by status.
    Supports pagination via offset and limit query parameters.
    """
    tasks = await asyncio.to_thread(task_store.list_tasks, status, offset, limit)
    return [_task_to_response(task) for task in tasks]

@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(request: Request, task_id: str):
    """
    Get detailed information about a specific task.
    Includes subtasks, events, and context keys.
    """
    try:
        task = await asyncio.to_thread(task_store.get_task, task_id)
        subtasks = await asyncio.to_thread(task_store.get_subtasks_for_task, task_id)
        events = await asyncio.to_thread(task_store.get_events, task_id)
        context_keys = await asyncio.to_thread(task_store.get_context_keys, task_id)

        return TaskDetailResponse(
            id=task["id"],
            goal=task["goal"],
            status=task["status"],
            execution_mode=task["execution_mode"],
            working_directory=task.get("working_directory"),
            created_at=task["created_at"],
            created_at_relative=format_relative_time(task["created_at"]),
            updated_at=task["updated_at"],
            subtasks=[SubtaskResponse(**subtask) for subtask in subtasks],
            events=[EventResponse(**event) for event in events],
            context_keys=context_keys
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

@router.get("/{task_id}/context/{key}")
async def get_task_context(request: Request, task_id: str, key: str):
    """
    Get the raw context value for a specific key.
    Returns plain text content.
    """
    try:
        value = task_store.get_context(task_id, key)
        if value is None:
            raise KeyError
        return value
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Context key '{key}' not found for task {task_id}")

@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_task(request: Request, task_id: str):
    """
    Mark a task as archived.
    Note: This does not stop in-progress background threads.
    """
    try:
        await asyncio.to_thread(task_store.update_task_status, task_id, "archived")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return

@router.post("/{task_id}/retry", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_task(request: Request, task_id: str):
    """
    Reset a task and all its subtasks, then re-run from scratch.
    Subtask statuses are batch-updated in a single UPDATE statement.
    """
    try:
        await asyncio.to_thread(task_store.get_task, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    await asyncio.to_thread(task_store.update_task_status, task_id, "planning")
    await asyncio.to_thread(task_store.reset_subtasks_for_task, task_id)
    await asyncio.to_thread(
        task_store.log_event, task_id, "task_planned", "Task retry initiated"
    )

    await asyncio.to_thread(maestro.run_background, task_id)

    refreshed = await asyncio.to_thread(task_store.get_task, task_id)
    return _task_to_response(refreshed)
