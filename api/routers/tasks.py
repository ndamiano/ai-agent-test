from fastapi import APIRouter, HTTPException, status, WebSocket, Query
from typing import List, Optional
import asyncio
from api.models.requests import CreateTaskRequest
from api.models.responses import TaskResponse, TaskDetailResponse, SubtaskResponse, EventResponse
from agents.task_runner import task_runner
from database.task_store import task_store
from api.websocket.manager import manager

router = APIRouter()

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(request: CreateTaskRequest):
    """
    Create and run a task in the background.
    Returns immediately with task details while the task runs asynchronously.
    """
    task_id = await asyncio.to_thread(
        task_runner.create_and_run_background,
        goal=request.goal,
        execution_mode=request.execution_mode,
        broadcast_fn=lambda event: manager.broadcast_sync(event['task_id'], event),
    )
    task = await asyncio.to_thread(task_store.get_task, task_id)
    return TaskResponse(**task)

@router.get("/", response_model=List[TaskResponse])
async def get_tasks(
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """
    List all tasks, optionally filtered by status.
    Supports pagination via offset and limit query parameters.
    """
    tasks = await asyncio.to_thread(task_store.list_tasks, status, offset, limit)
    return [TaskResponse(**task) for task in tasks]

@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str):
    """
    Get detailed information about a specific task.
    Includes subtasks, events, and context keys.
    """
    try:
        task = task_store.get_task(task_id)
        subtasks = task_store.get_subtasks_for_task(task_id)
        events = task_store.get_events(task_id)
        context_keys = list(task_store.get_all_context(task_id).keys())

        return TaskDetailResponse(
            id=task["id"],
            goal=task["goal"],
            status=task["status"],
            execution_mode=task["execution_mode"],
            created_at=task["created_at"],
            updated_at=task["updated_at"],
            subtasks=[SubtaskResponse(**subtask) for subtask in subtasks],
            events=[EventResponse(**event) for event in events],
            context_keys=context_keys
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

@router.get("/{task_id}/context/{key}")
async def get_task_context(task_id: str, key: str):
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
async def cancel_task(task_id: str):
    """
    Mark a task as cancelled.
    Note: This does not stop in-progress background threads.
    """
    try:
        task_store.update_task_status(task_id, "cancelled")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return

@router.websocket("/{task_id}/ws")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint for real-time task updates.
    Sends task status on connect and all subsequent events.
    """
    await manager.connect(task_id, websocket)

    try:
        # Send initial task status
        task = task_store.get_task(task_id)
        await websocket.send_json({
            "type": "task_status",
            "task_id": task_id,
            "task": task
        })

        # Keep the connection open until client disconnects
        while True:
            # Wait for client messages (if any) or just keep connection alive
            data = await websocket.receive_text()
            # For now, we don't process client messages, just keep connection alive
    except Exception as e:
        # Handle task not found or other errors
        if "not found" in str(e).lower():
            await websocket.send_json({
                "type": "error",
                "message": f"Task {task_id} not found",
                "task_id": task_id
            })
        # Client disconnected or other error
        pass
    finally:
        manager.disconnect(task_id, websocket)
