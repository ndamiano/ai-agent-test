"""The PROMPT LOG — operator-only, read-only, every game.

Nothing instruments the build to make this work: an llm turn IS a jobs row, its payload IS the
exact Responses body that went to the model (instructions + input + tools) and its result IS the
reply, so the whole conversation behind any game is readable after the fact. This router only
reshapes those rows for reading; `require_admin` gates all of it, since a bucket list spans every
user's games.
"""

from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException

from auth.deps import require_admin
from auth.store import User
from db import store as db_store
from llm_clients.wire import responses_to_chat

router = APIRouter()


@router.get("/games", response_model=List[Dict])
async def prompt_buckets(_: User = Depends(require_admin)) -> List[Dict]:
    """Every game that has spent llm turns, plus the platform bucket (game_id null: chat and spec
    drafting, which run before there is a game to charge)."""
    return db_store.llm_turn_buckets()


@router.get("/turns", response_model=List[Dict])
async def prompt_turns(scope: Literal["all", "game", "platform"] = "all",
                       game_id: Optional[str] = None,
                       limit: int = 2000,
                       _: User = Depends(require_admin)) -> List[Dict]:
    """The turn index for one bucket, oldest first — sizes and the system prompt's head, no bodies."""
    if scope == "game":
        if not game_id:
            raise HTTPException(status_code=400, detail="scope=game needs a game_id")
        return db_store.llm_turns_for_game(game_id, limit=limit)
    if scope == "platform":
        return db_store.llm_turns_platform(limit=limit)
    return db_store.llm_turns_all(limit=limit)


@router.get("/turns/{job_id}", response_model=Dict)
async def prompt_turn(job_id: str, _: User = Depends(require_admin)) -> Dict:
    """One turn, reconstructed: the system prompt, the messages, the offered tools, the reply."""
    job = db_store.get_job(job_id)
    if job is None or job["queue"] != "llm":
        raise HTTPException(status_code=404, detail=f"no llm turn {job_id!r}")
    return _turn_view(job)


def _turn_view(job: Dict) -> Dict:
    """One stored turn, reshaped for reading. The shape is read off the RECORD, never asked of the
    current connector: `llm.api` can change between a build and someone reading its log, and a db
    that has served both wire formats holds both."""
    body = (job.get("payload") or {}).get("body") or {}
    system, messages = _view_conversation(body)
    return {
        "id": job["id"], "game_id": job["game_id"], "build_id": job["build_id"],
        "status": job["status"], "model": body.get("model") or job["model"],
        "created_at": job["created_at"], "exec_seconds": job["exec_seconds"],
        "error": job["error"], "stage": (job.get("metadata") or {}).get("stage"),
        "reasoning": (body.get("reasoning") or {}).get("effort"),
        "max_output_tokens": body.get("max_output_tokens") or body.get("max_tokens"),
        "system": system,
        "messages": messages,
        "tools": [_view_tool(t) for t in body.get("tools") or []],
        "response": _view_response(job.get("result")),
    }


def _view_conversation(body: Dict) -> tuple:
    """(system, messages) from either wire format: Responses puts the system prompt in
    `instructions` and the turns in `input`; chat puts both in `messages`."""
    if body.get("input") is not None:
        return body.get("instructions") or "", [_view_message(i) for i in body["input"]]
    system, out = "", []
    for m in body.get("messages") or []:
        if m.get("role") == "system" and not system:
            system = m.get("content") or ""
            continue
        out.append(_view_chat_message(m))
    return system, out


def _view_chat_message(m: Dict) -> Dict:
    if m.get("role") == "tool":
        return {"role": "tool", "kind": "tool_result", "name": m.get("tool_call_id"),
                "text": m.get("content") or ""}
    calls = m.get("tool_calls") or []
    if calls:
        fn = calls[0].get("function") or {}
        return {"role": "assistant", "kind": "tool_call", "name": fn.get("name"),
                "text": fn.get("arguments", "")}
    return {"role": m.get("role", "user"), "kind": "text", "name": None,
            "text": m.get("content") or ""}


def _view_tool(t: Dict) -> Dict:
    """Responses tools are flat; chat tools nest under `function`."""
    fn = t.get("function") if isinstance(t.get("function"), dict) else t
    return {"name": fn.get("name"), "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {})}


def _view_message(item: Dict) -> Dict:
    """One Responses input item as something readable. The transcript a fix turn re-sends carries
    the model's own tool calls and their results, not just prose — render those as themselves."""
    kind = item.get("type")
    if kind == "function_call":
        return {"role": "assistant", "kind": "tool_call", "name": item.get("name"),
                "text": item.get("arguments", "")}
    if kind == "function_call_output":
        return {"role": "tool", "kind": "tool_result", "name": item.get("call_id"),
                "text": item.get("output", "")}
    content = item.get("content")
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return {"role": item.get("role", "user"), "kind": "text", "name": None, "text": content or ""}


def _view_response(result: Optional[Dict]) -> Optional[Dict]:
    if not result:
        return None
    chat = result if result.get("choices") else responses_to_chat(result)
    message = (chat.get("choices") or [{}])[0].get("message") or {}
    return {
        "text": message.get("content") or "",
        "tool_calls": [{"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
                       for tc in message.get("tool_calls") or []],
        "usage": chat.get("usage") or {},
    }
