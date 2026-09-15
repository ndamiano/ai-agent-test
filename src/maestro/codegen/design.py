"""The design stage: four llm calls write the build spec, landed as design/design.md in the game
folder, with the ask itself as the fallback prompt."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from db import games, jobs
from llm_clients.connector import get_connector
from llm_clients.message_builder import MessageBuilder

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).parent / "prompts"
_SYSTEM = "You design browser games."
MAX_TOKENS = 100000
DESIGN_DIR = "design"
STEPS = ("gameplay", "visual", "engineering", "integrate")
SECTIONS = [str(n) for n in range(12)] + ["A"]
CONTINUATIONS = 3
RETRIES = 1
_HEADING = re.compile(r"^#+\s*(\d+|A)\.", re.M)


def _prompt(name: str, **slots) -> str:
    text = (_PROMPTS / "design" / f"{name}.txt").read_text(encoding="utf-8")
    for key, value in slots.items():
        text = text.replace("{" + key + "}", value)
    return text


def _messages(step: str, ask: str, docs: Dict[str, str], spec_so_far: str = "") -> list:
    body = _prompt(step, request=ask, **docs)
    if step == "integrate" and spec_so_far:
        body += "\n\n" + _prompt("integrate_continue", spec_so_far=spec_so_far)
    return MessageBuilder(_SYSTEM).add_user(body).build()


def _design_dir(run_id: str) -> Path:
    from maestro.state import RunState
    d = RunState(run_id).run_dir / DESIGN_DIR
    d.mkdir(exist_ok=True)
    return d


def _docs(run_id: str) -> Dict[str, str]:
    d = _design_dir(run_id)
    return {s: (d / f"{s}.md").read_text(encoding="utf-8")
            for s in STEPS[:3] if (d / f"{s}.md").is_file()}


def missing_sections(text: str) -> List[str]:
    have = set(_HEADING.findall(text))
    return [n for n in SECTIONS if n not in have]


def _reply_text(result: Optional[Dict]) -> str:
    msg = ((result or {}).get("choices") or [{}])[0].get("message", {}) or {}
    return msg.get("content") or ""


def _request(ask: str) -> str:
    return (_PROMPTS / "request.txt").read_text(encoding="utf-8").replace("{ask}", ask).strip()


def _land(run_id: str, request: str) -> str:
    from maestro.codegen.run import set_prompt
    set_prompt(run_id, request, event="prompt_proposed")
    return request


def _land_spec(run_id: str, spec: str, ask: str) -> str:
    from maestro.codegen.staging import spec_path
    from maestro.state import RunState
    spec_path(RunState(run_id).run_dir).write_text(spec, encoding="utf-8")
    return _land(run_id, _request(ask))


def _fallback(run_id: str, build_id: Optional[str], ask: str, why: str) -> None:
    logger.warning("design for %s: %s — the ask is the prompt", run_id, why)
    if build_id:
        games.build_finished(build_id, "failed")
    _land(run_id, ask)
    _start_build(run_id)


def _start_build(run_id: str) -> None:
    from maestro.codegen import build_chain
    try:
        build_chain.kickoff(run_id, kind="build")
    except Exception:
        logger.exception("build for %s could not start after its design landed", run_id)


def _enqueue_step(run_id: str, build_id: str, ask: str, step: str, spec_so_far: str = "") -> None:
    conn = get_connector()
    payload, model = conn.build_llm_job(_messages(step, ask, _docs(run_id), spec_so_far), [],
                                        MAX_TOKENS)
    jobs.enqueue_job("llm", payload, game_id=run_id, build_id=build_id, model=model,
                     metadata={"stage": "design", "run_id": run_id, "step": step})


def enqueue(run_id: str, ask: str) -> None:
    build_id = games.create_build(run_id, kind="design")
    games.build_started(build_id)
    try:
        _enqueue_step(run_id, build_id, ask, "gameplay")
    except Exception:
        logger.exception("design for %s could not be enqueued", run_id)
        games.build_finished(build_id, "failed")
        _land(run_id, ask)


def _integrator_ready(run_id: str) -> bool:
    """True for exactly one of the two callers racing here, so the integrator is asked for once."""
    d = _design_dir(run_id)
    if not all((d / f"{s}.md").is_file() for s in ("visual", "engineering")):
        return False
    try:
        os.mkdir(d / ".integrating")
    except FileExistsError:
        return False
    return True


def _retry(run_id: str, step: str) -> bool:
    d = _design_dir(run_id)
    marker = d / f".retried-{step}"
    n = int(marker.read_text()) if marker.is_file() else 0
    if n >= RETRIES:
        return False
    marker.write_text(str(n + 1))
    return True


def on_complete(run_id: str, build_id: Optional[str], result: Optional[Dict],
                error: Optional[str], step: str) -> None:
    from maestro.state import RunState
    ask = (RunState(run_id).read_spec() or {}).get("ask", "")
    text = _reply_text(result) if error is None else ""
    d = _design_dir(run_id)
    if not text.strip():
        if error is None and _retry(run_id, step):
            spec = d / "spec.md"
            _next(run_id, build_id, ask, step,
                  spec_so_far=spec.read_text(encoding="utf-8") if step == "integrate" and spec.is_file() else "")
            return
        _fallback(run_id, build_id, ask, f"{step} landed with nothing ({error or 'empty reply'})")
        return
    if step == "integrate":
        spec = d / "spec.md"
        text = (spec.read_text(encoding="utf-8") if spec.is_file() else "") + text
        spec.write_text(text, encoding="utf-8")
        rounds = int((d / ".rounds").read_text()) if (d / ".rounds").is_file() else 0
        if missing_sections(text) and rounds < CONTINUATIONS:
            (d / ".rounds").write_text(str(rounds + 1))
            _next(run_id, build_id, ask, "integrate", spec_so_far=text)
            return
        if build_id:
            games.build_finished(build_id, "succeeded", steps=1)
        _land_spec(run_id, text, ask)
        _start_build(run_id)
        return
    (d / f"{step}.md").write_text(text, encoding="utf-8")
    if step == "gameplay":
        _next(run_id, build_id, ask, "visual")
        _next(run_id, build_id, ask, "engineering")
    elif _integrator_ready(run_id):
        _next(run_id, build_id, ask, "integrate")


def _next(run_id: str, build_id: Optional[str], ask: str, step: str, spec_so_far: str = "") -> None:
    try:
        _enqueue_step(run_id, build_id or "", ask, step, spec_so_far)
    except Exception:
        logger.exception("design for %s: %s could not be enqueued", run_id, step)
        _fallback(run_id, build_id, ask, f"{step} refused")


def _call(step: str, ask: str, docs: Dict[str, str], spec_so_far: str = "") -> str:
    for _ in range(RETRIES + 1):
        text = _reply_text(get_connector().generate_with_tools(
            _messages(step, ask, docs, spec_so_far), [], max_tokens=MAX_TOKENS))
        if text.strip():
            return text
    raise ValueError(f"{step} came back empty")


def generate(run_id: str, ask: str) -> str:
    from tools.execution_context import run_scope
    build_id = games.create_build(run_id, kind="design")
    games.build_started(build_id)
    d = _design_dir(run_id)
    try:
        with run_scope(run_id, build_id):
            for step in STEPS[:3]:
                (d / f"{step}.md").write_text(_call(step, ask, _docs(run_id)), encoding="utf-8")
            spec = ""
            for _ in range(CONTINUATIONS + 1):
                spec += _call("integrate", ask, _docs(run_id), spec)
                if not missing_sections(spec):
                    break
        if not spec.strip():
            raise ValueError("the integrator came back empty")
        (d / "spec.md").write_text(spec, encoding="utf-8")
        games.build_finished(build_id, "succeeded", steps=1)
        return _land_spec(run_id, spec, ask)
    except Exception as e:
        logger.exception("design for %s failed (%s) — the ask is the prompt", run_id, e)
    games.build_finished(build_id, "failed", steps=1)
    return _land(run_id, ask)
