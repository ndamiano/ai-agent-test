from fastapi import APIRouter, HTTPException, status
from typing import List, Optional
from api.models.requests import CreateTaskRequest, AskRequest
from api.models.responses import TaskResponse, TaskDetailResponse, SubtaskResponse, EventResponse, AskResponse
from agents.task_runner import task_runner
from database.task_store import task_store

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    responses={404: {"description": "Task not found"}}
)

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(request: CreateTaskRequest):
    """
    Create and run a task in the background.
    Returns immediately with task details while the task runs asynchronously.
    """
    task_id = task_runner.create_and_run_background(
        goal=request.goal,
        execution_mode=request.execution_mode
    )
    task = task_store.get_task(task_id)
    return TaskResponse(**task)

@router.get("/", response_model=List[TaskResponse])
async def get_tasks(status: Optional[str] = None):
    """
    List all tasks, optionally filtered by status.
    """
    tasks = task_store.list_tasks(status)
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

@router.post("/{task_id}/ask", response_model=AskResponse)
async def ask_task(task_id: str, request: AskRequest):
    """
    Ask a question about the task's context.
    Returns the answer and which context keys were used.
    """
    try:
        answer = task_runner.ask(task_id, request.question)
        # Note: The current implementation doesn't track which context keys were used
        # This would need to be enhanced in the task_runner.ask method
        return AskResponse(question=request.question, answer=answer, context_used=[])
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Error querying task {task_id}: {str(e)}")

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
