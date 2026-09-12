"""The DESIGN stage — the user's words go in, a systems design comes out, the design is the
run's PROMPT, and the build starts on it.

Measured 2026-08-26: the same model given a request built an unplayable game in 153 turns; given a
1,400-word systems design of that request (written by itself, from prompts/design.txt) it built
the whole game in 58. The ask is kept verbatim as the record of what was wanted.

What it writes, in spec.json:
  {"ask", "title"}             designing — the ask is the user's words verbatim, the title is cut
                               from them; `request` is ABSENT, never empty, until the design lands
  {"ask", "title", "request"}  designed — `request` is what the build sends
Every landing emits `prompt_proposed`. The FALLBACK is the ask itself as the request: a design that
fails, is refused by the compute budget, or comes back empty must never cost the user their build.

  enqueue(run_id, ask)              web: ONE llm job (metadata.stage="design") and return; the
                                    control plane routes its completion to on_complete
  generate(run_id, ask) -> str      CLI: the same call, synchronous; returns the request that landed
  on_complete(run_id, result, error) the completion handler: lands the prompt and STARTS THE BUILD.
                                    A kickoff that is refused leaves the run designed and idle,
                                    and the page's Build button re-tries it.

The run must already be charged: the job is admitted against the game's compute budget like any
build turn.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from db import games, jobs
from llm_clients.connector import get_connector
from llm_clients.message_builder import MessageBuilder

logger = logging.getLogger(__name__)

_PROMPT = Path(__file__).parent / "prompts" / "design.txt"
_SYSTEM = "You design browser games."
MAX_TOKENS = 100000


def _messages(ask: str) -> list:
    return MessageBuilder(_SYSTEM).add_user(
        _PROMPT.read_text(encoding="utf-8").format(request=ask)).build()


def _land(run_id: str, request: str) -> str:
    from maestro.codegen.run import set_prompt
    set_prompt(run_id, request, event="prompt_proposed")
    return request


def _design_of(result: Optional[Dict]) -> str:
    msg = ((result or {}).get("choices") or [{}])[0].get("message", {}) or {}
    return (msg.get("content") or "").strip()


def enqueue(run_id: str, ask: str) -> None:
    conn = get_connector()
    payload, model = conn.build_llm_job(_messages(ask), [], MAX_TOKENS)
    build_id = games.create_build(run_id, kind="design")
    games.build_started(build_id)
    try:
        jobs.enqueue_job("llm", payload, game_id=run_id, build_id=build_id, model=model,
                             metadata={"stage": "design", "run_id": run_id})
    except Exception:
        logger.exception("design for %s could not be enqueued — the ask is the prompt", run_id)
        games.build_finished(build_id, "failed")
        _land(run_id, ask)


def generate(run_id: str, ask: str) -> str:
    from tools.execution_context import run_scope
    build_id = games.create_build(run_id, kind="design")
    games.build_started(build_id)
    try:
        with run_scope(run_id, build_id):
            reply = get_connector().generate_with_tools(_messages(ask), [], max_tokens=MAX_TOKENS)
        text = _design_of(reply)
        if text:
            games.build_finished(build_id, "succeeded", steps=1)
            return _land(run_id, text)
        logger.warning("design for %s came back empty (%s) — the ask is the prompt",
                       run_id, str(reply)[:200])
    except Exception:
        logger.exception("design for %s failed — the ask is the prompt", run_id)
    games.build_finished(build_id, "failed", steps=1)
    return _land(run_id, ask)


def on_complete(run_id: str, result: Optional[Dict], error: Optional[str]) -> None:
    from maestro.state import RunState
    ask = (RunState(run_id).read_spec() or {}).get("ask", "")
    text = _design_of(result) if error is None else ""
    if not text:
        logger.warning("design for %s landed with nothing (%s) — the ask is the prompt",
                       run_id, error or "empty reply")
    _land(run_id, text or ask)
    from maestro.codegen import build_chain
    try:
        build_chain.kickoff(run_id, kind="build")
    except Exception:
        logger.exception("build for %s could not start after its design landed", run_id)
