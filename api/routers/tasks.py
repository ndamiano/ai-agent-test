import asyncio
import json
import logging
import threading
from fastapi import APIRouter, HTTPException, status, Query, Request
from pydantic import BaseModel
from typing import List, Optional
from api.models.requests import CreateTaskRequest
from api.models.responses import TaskResponse, TaskDetailResponse, SubtaskResponse, EventResponse
from agents.maestro_agent import MaestroAgent
from agents.refiner_agent import RefinerAgent
from database.task_store import task_store
from api.websocket.event_bus import event_bus
from config.settings_manager import settings_manager
from config.time_utils import format_relative_time, get_utc_timestamp

logger = logging.getLogger(__name__)

maestro = MaestroAgent()
refiner = RefinerAgent()

router = APIRouter()


class RefineMessageRequest(BaseModel):
    message: str


class ConfirmRefineRequest(BaseModel):
    refined_goal: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None

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
    If refine_before_execution is enabled, task enters 'refining' state for
    interactive goal refinement before the agent pipeline starts.
    """
    task_dict = await asyncio.to_thread(
        task_store.create_task,
        task_request.goal,
        task_request.execution_mode or "sequential",
        task_request.working_directory
    )
    task_id = task_dict["id"]
    goal = task_request.goal
    logger.info(f"Task created: {task_id}")

    await event_bus.publish({
        'type': 'task_created',
        'task_id': task_id,
        'goal': goal,
        'timestamp': get_utc_timestamp()
    })

    settings = settings_manager.get_settings()
    if settings.get("refine_before_execution", False):
        # Enter refining mode — generate the opening AI question in background
        await asyncio.to_thread(task_store.update_task_status, task_id, "refining")
        task = await asyncio.to_thread(task_store.get_task, task_id)
        await event_bus.publish({'type': 'task_status', 'task_id': task_id, 'task': task})

        def _generate_opening():
            try:
                response = refiner.generate_response(goal, [])
                messages = [{"role": "assistant", "content": response, "timestamp": get_utc_timestamp()}]
                task_store.set_refine_messages(task_id, messages)
                event_bus.publish_sync({
                    'type': 'refine_message',
                    'task_id': task_id,
                    'messages': messages,
                    'timestamp': get_utc_timestamp(),
                })
            except Exception as e:
                logger.error(f"Refiner opening message failed for task {task_id}: {e}")

        thread = threading.Thread(target=_generate_opening, daemon=True)
        thread.start()
    else:
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
        child_tasks = await asyncio.to_thread(task_store.get_child_tasks, task_id)

        return TaskDetailResponse(
            id=task["id"],
            goal=task["goal"],
            status=task["status"],
            execution_mode=task["execution_mode"],
            working_directory=task.get("working_directory"),
            parent_task_id=task.get("parent_task_id"),
            created_at=task["created_at"],
            created_at_relative=format_relative_time(task["created_at"]),
            updated_at=task["updated_at"],
            subtasks=[SubtaskResponse(
                **{k: v for k, v in subtask.items() if k != 'input_context'},
                child_task_id=(subtask.get('input_context') or {}).get('child_task_id') if subtask.get('agent_id') == 'maestro' else None,
            ) for subtask in subtasks],
            events=[EventResponse(**event) for event in events],
            context_keys=context_keys,
            child_tasks=[TaskResponse(
                **ct,
                created_at_relative=format_relative_time(ct["created_at"]),
                subtasks=[SubtaskResponse(**{k: v for k, v in cs.items() if k not in ('input_context',)})
                          for cs in await asyncio.to_thread(task_store.get_subtasks_for_task, ct["id"])],
            ) for ct in child_tasks],
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

@router.post("/{task_id}/refine")
async def send_refine_message(request: Request, task_id: str, body: RefineMessageRequest):
    """
    Send a user message in the refinement conversation and get an AI response.
    Task must be in 'refining' status.
    """
    try:
        task = await asyncio.to_thread(task_store.get_task, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task["status"] != "refining":
        raise HTTPException(status_code=409, detail="Task is not in refining state")

    messages = await asyncio.to_thread(task_store.get_refine_messages, task_id)
    messages.append({"role": "user", "content": body.message, "timestamp": get_utc_timestamp()})

    try:
        response = await asyncio.to_thread(refiner.generate_response, task["goal"], messages)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Refiner LLM call failed: {e}")

    messages.append({"role": "assistant", "content": response, "timestamp": get_utc_timestamp()})
    await asyncio.to_thread(task_store.set_refine_messages, task_id, messages)

    await event_bus.publish({
        'type': 'refine_message',
        'task_id': task_id,
        'messages': messages,
        'timestamp': get_utc_timestamp(),
    })

    return {"messages": messages}


@router.post("/{task_id}/synthesize")
async def synthesize_refine(request: Request, task_id: str):
    """
    Synthesize the refinement conversation into a structured output (enriched goal +
    acceptance criteria) WITHOUT starting execution. Returns the synthesis for the
    user to review and edit before confirming.
    """
    try:
        task = await asyncio.to_thread(task_store.get_task, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task["status"] != "refining":
        raise HTTPException(status_code=409, detail="Task is not in refining state")

    messages = await asyncio.to_thread(task_store.get_refine_messages, task_id)
    try:
        synthesis = await asyncio.to_thread(refiner.synthesize, task["goal"], messages)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Synthesis LLM call failed: {e}")

    return synthesis


@router.post("/{task_id}/confirm", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def confirm_refine(request: Request, task_id: str, body: ConfirmRefineRequest):
    """
    Confirm the (user-reviewed, possibly edited) refined goal and acceptance criteria,
    then hand off to the agent pipeline.
    """
    try:
        task = await asyncio.to_thread(task_store.get_task, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task["status"] != "refining":
        raise HTTPException(status_code=409, detail="Task is not in refining state")

    # Enter synthesizing state briefly while we persist and start the pipeline
    await asyncio.to_thread(task_store.update_task_status, task_id, "synthesizing")
    synthesizing_task = await asyncio.to_thread(task_store.get_task, task_id)
    await event_bus.publish({'type': 'task_status', 'task_id': task_id, 'task': synthesizing_task})

    refined_goal = body.refined_goal or task["goal"]
    criteria = body.acceptance_criteria or []

    # Persist enriched goal and pre-seed acceptance criteria for maestro
    await asyncio.to_thread(task_store.update_task_goal, task_id, refined_goal)
    if criteria:
        await asyncio.to_thread(
            task_store.write_context, task_id, "acceptance_criteria", json.dumps(criteria)
        )

    # Hand off to maestro
    await asyncio.to_thread(maestro.run_background, task_id)

    refreshed = await asyncio.to_thread(task_store.get_task, task_id)
    return _task_to_response(refreshed)


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
