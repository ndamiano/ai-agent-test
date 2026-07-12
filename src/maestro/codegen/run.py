"""Codegen run orchestrator + CLI.

  create_run(user_id)          → a fresh run dir
  draft_spec(request)          → the local model drafts a design SPEC (stage 1)
  run_build(run_id)            → the AgentLoop drives CodegenModule to a passing game.js (stage 2)
  python -m maestro.codegen.run [--yes] "<request>"  → draft → freeze (your ok) → build → play path

The build refuses until the spec is frozen. One game per run: runs/<id>/game.js.
"""

import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path

from maestro.state import RunState

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"


def create_run(user_id: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    state = RunState.for_run(run_id)
    state.write_owner(user_id)
    return run_id


def _content(resp) -> str:
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def draft_spec(request: str) -> dict:
    """Stage 1: prose request → design SPEC JSON. Prose-in, JSON-out — the easy half."""
    from llm_clients.connector_selector import get_connector
    from llm_clients.message_builder import MessageBuilder

    system = (_PROMPTS / "spec_draft.txt").read_text(encoding="utf-8")
    msgs = MessageBuilder(system).add_user(f"Request: {request}\n\nWrite the JSON spec.").build()
    reply = _content(get_connector().generate_with_tools(msgs, [], max_tokens=4000))
    m = re.search(r"```(?:json)?\s*\n(.*?)```", reply, re.S)
    design = json.loads(m.group(1) if m else reply)
    return {"request": request, "title": design.get("title", request),
            "mode": design.get("mode", "2d"), "design": design, "frozen": False}


def freeze(run_id: str) -> None:
    state = RunState.for_run(run_id)
    spec = state.read_spec()
    spec["frozen"] = True
    state.write_spec(spec)


def run_build(run_id: str, max_steps: int = 60):
    """Stage 2: drive CodegenModule until game.js passes the local gates. The surviving AgentLoop
    does the driving — collect errors (authored/runs/plays), fix the top one, repeat."""
    from maestro.agent_loop import AgentLoop
    from maestro.codegen.module import CodegenModule
    from maestro.codegen.tools import build_codegen_tools
    from maestro.run_control import get_or_create, remove
    from llm_clients.connector_selector import get_connector

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no spec for run {run_id!r} — draft one first")
    tools = build_codegen_tools(state)
    control = get_or_create(run_id)
    loop = AgentLoop(spec, state, [CodegenModule()], tools, connector=get_connector(),
                     max_steps=max_steps, control=control)
    t0 = time.perf_counter()
    try:
        result = loop.run()
    finally:
        remove(run_id)
    result.elapsed = time.perf_counter() - t0
    logger.info("codegen build %s: ok=%s steps=%d elapsed=%.1fs",
                run_id, result.ok, result.steps, result.elapsed)
    return result


def fix_from_note(run_id: str, note: str, max_steps: int = 40):
    """Patch a built game from a HUMAN playtest note (the local play-critic: the human is the eye the
    headless gates aren't). One targeted patch from the note, then re-run the loop so any gate the
    patch regresses is re-fixed before shipping."""
    from maestro.codegen.gates import extract_code, game_files, stage_for_play
    from maestro.codegen.module import _kit_doc, _FILE_RE, _CODE_MAX_TOKENS, _PROMPTS
    from maestro.codegen.tools import build_codegen_tools
    from llm_clients.message_builder import MessageBuilder
    from llm_clients.connector_selector import get_connector

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")
    files = game_files(state.run_dir)
    bodies = "\n\n".join(f"## FILE: {n}\n```js\n{s}\n```" for n, s in files.items())
    failure = ("HUMAN PLAYTEST FEEDBACK — the game passed the automated gates but is WRONG when a "
               f"person plays it. Fix exactly this:\n{note}")
    user = "\n\n".join([
        f"# KIT API\n{_kit_doc(spec)}",
        f"# THE GAME (every file)\n{bodies}",
        f"# FAILURE (change as few files as possible — ideally one)\n{failure}",
        "Reply with a line `FILE: <name.js>` then that file's COMPLETE new source as one ```js block.",
    ])
    msgs = MessageBuilder((_PROMPTS / "fix_file.txt").read_text(encoding="utf-8")).add_user(user).build()
    text = _content(get_connector().generate_with_tools(msgs, [], max_tokens=_CODE_MAX_TOKENS))
    m = _FILE_RE.search(text)
    target = m.group(1) if m else ("main.js" if "main.js" in files else next(iter(files), "main.js"))
    build_codegen_tools(state)["write_game_file"](code=extract_code(text), file=target)
    result = run_build(run_id, max_steps=max_steps)   # re-gate + auto-fix any regression the patch caused
    if result.ok:
        stage_for_play(state.run_dir, run_id)
    return result


def _cli(request: str, *, yes: bool = False) -> int:
    from auth import store
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

    users = store.list_users()
    if not users:
        print("no accounts yet — create one first: python -m auth.cli create <handle>")
        return 1
    owner = users[0].id

    run_id = create_run(owner)
    print(f"run: {run_id}\ndrafting spec for: {request!r}\n")
    spec = draft_spec(request)
    RunState.for_run(run_id).write_spec(spec)
    print(json.dumps(spec["design"], indent=2, ensure_ascii=False))

    if not yes and input("\nFreeze this spec and build? [y/N] ").strip().lower() != "y":
        print("Not frozen. Edit spec.json and re-run, or freeze later.")
        return 0

    freeze(run_id)
    print("\nfrozen — building...\n")
    result = run_build(run_id)

    mins, secs = divmod(int(result.elapsed), 60)
    print(f"\nok={result.ok}  steps={result.steps}  elapsed={mins}m{secs:02d}s")
    if not result.ok:
        for e in result.failures:
            print(f"  unmet: [{e.component}] {e.code}: {e.message[:200]}")
    state = RunState.for_run(run_id)
    from maestro.codegen.gates import entry_path
    print(f"game: {entry_path(state.run_dir).resolve()}")
    if result.ok:
        from maestro.codegen.gates import stage_for_play
        print(f"play: runtime/{stage_for_play(state.run_dir, run_id)}")
    return 0 if result.ok else 1


def _cli_fix(run_id: str, note: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    print(f"fixing {run_id} from note: {note!r}\n")
    result = fix_from_note(run_id, note)
    print(f"\nok={result.ok}  steps={result.steps}")
    if result.ok:
        print(f"play: runtime/index.html?game={run_id}")
    else:
        for e in result.failures:
            print(f"  unmet: [{e.component}] {e.code}: {e.message[:200]}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--fix":
        if len(sys.argv) < 4:
            sys.exit('usage: python -m maestro.codegen.run --fix <run_id> "<what is wrong>"')
        sys.exit(_cli_fix(sys.argv[2], " ".join(sys.argv[3:])))
    args = [a for a in sys.argv[1:] if a != "--yes"]
    if not args:
        sys.exit('usage: python -m maestro.codegen.run [--yes] "<request>"   |   --fix <run_id> "<note>"')
    sys.exit(_cli(" ".join(args), yes="--yes" in sys.argv[1:]))
