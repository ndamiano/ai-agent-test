"""Run orchestrator + CLI.

  create_run(user_id)          → a fresh run dir
  draft_spec(request)          → the local model drafts a BRIEF (stage 1)
  run_build(run_id)            → kick the build off + BLOCK-poll the cursor to done (CLI only; the web
                                 path is fire-and-forget via build_chain.kickoff — stage 2)
  python -m maestro.codegen.run "<request>"  → draft → freeze (your ok) → build → play path

The build refuses until the brief is frozen. The build itself is a chain of llm jobs driven by
build_chain's completion handler, so `run_build`/`fix_from_note` only START it and wait — the API
server (where worker completions land) must be up, same as every other queue stage.
"""

import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from auth import store
from auth.billing import SECONDS_PER_CREDIT
from db import store as db_store
from llm_clients.connector import get_connector
from llm_clients.message_builder import MessageBuilder
from maestro.codegen import build_chain, build_state
from maestro.codegen.staging import game_dir, is_staged
from maestro.state import RunState
from tools.build_events import _emit

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1.0
_PROMPTS = Path(__file__).resolve().parent / "prompts"


class BuildResult:
    """The CLI's view of a finished build (the web path is fire-and-forget and reads status/events
    instead)."""

    def __init__(self, ok: bool, steps: int, elapsed: float, summary: str = ""):
        self.ok = ok
        self.steps = steps
        self.elapsed = elapsed
        self.summary = summary


def create_run(user_id: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    RunState(run_id)
    db_store.create_game(run_id, user_id)
    return run_id


def _content(resp) -> str:
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def draft_spec(request: str) -> dict:
    """Stage 1: prose request → a short BRIEF (look, audio, scope, and the promises the request
    actually made). Retries on a bad JSON parse."""
    conn = get_connector()
    system = (_PROMPTS / "spec_draft.txt").read_text(encoding="utf-8")
    user = f"Request: {request}\n\nWrite the JSON brief."
    design, last = None, ""
    for _ in range(3):
        reply = _content(conn.generate_with_tools(
            MessageBuilder(system).add_user(user).build(), [], max_tokens=4000))
        m = re.search(r"```(?:json)?\s*\n(.*?)```", reply, re.S)
        try:
            design = json.loads(m.group(1) if m else reply)
            break
        except json.JSONDecodeError as e:
            last = f"{e} — return ONLY one ```json block of STRICT valid JSON, no trailing commas."
            user = f"Request: {request}\n\nYour previous JSON was invalid: {last}\n\nWrite the JSON brief."
    if design is None:
        raise ValueError(f"brief draft never produced valid JSON: {last}")
    return {"request": request, "title": design.get("title") or request,
            "design": design, "frozen": False}


def propose_spec(request: str, run_id: str) -> dict:
    """Draft a brief from the request, persist it to the run, and announce it for human review."""
    spec = draft_spec(request)
    RunState(run_id).write_spec(spec)
    _mirror_spec_meta(run_id, spec)
    _emit("spec_proposed", run_id, title=spec["title"], mode="")
    return spec


def amend_spec(run_id: str, note: str) -> dict:
    """Re-draft an existing brief from a free-text revision note. Writes it UNFROZEN so the build
    refuses until the human re-freezes."""
    state = RunState(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")
    augmented = (f"Original request: {spec['request']}\n"
                 f"Revision requested: {note}\n"
                 f"Current brief JSON: {json.dumps(spec['design'], ensure_ascii=False)}\n"
                 "Produce the full updated brief.")
    revised = draft_spec(augmented)
    revised["request"] = spec["request"]
    revised["frozen"] = False
    state.write_spec(revised)
    _mirror_spec_meta(run_id, revised)
    _emit("spec_amend_requested", run_id, note=note)
    return revised


def freeze_spec(run_id: str) -> dict:
    """The human's out-of-band approval: freeze the brief so the build may run."""
    state = RunState(run_id)
    spec = state.read_spec()
    spec["frozen"] = True
    state.write_spec(spec)
    _mirror_spec_meta(run_id, spec)
    _emit("spec_frozen", run_id, title=spec["title"])
    return {"ok": True, "frozen": True}


def _mirror_spec_meta(run_id: str, spec: dict) -> None:
    """spec.json is the source of truth; the games row mirrors its identity fields for listing."""
    db_store.update_spec_meta(run_id, spec.get("title", ""), "", bool(spec.get("frozen")))


def run_build(run_id: str, max_steps: Optional[int] = None) -> BuildResult:
    """Stage 2 (CLI/blocking): kick the build off, then poll the durable cursor to completion. The
    build itself is fire-and-forget — build_chain enqueues each llm turn and the control-plane's
    completion handler drives the next — so this only WAITS."""
    build_chain.kickoff(run_id, kind="build", max_steps=max_steps)
    return _await_build(run_id)


def fix_from_note(run_id: str, note: str, max_steps: int = 40) -> BuildResult:
    """Patch a built game from a HUMAN playtest note. The note becomes the build's request and the
    same turn machine reads its way in and changes what's wrong."""
    build_chain.kickoff(run_id, kind="fix", note=note, max_steps=max_steps)
    return _await_build(run_id)


def _await_build(run_id: str) -> BuildResult:
    """Block until the build's cursor reports done, then summarize it. CLI-only."""
    state = RunState(run_id)
    while True:
        cursor = build_state.load(state.run_dir)
        if cursor is not None and cursor.phase == "done":
            break
        time.sleep(_POLL_INTERVAL)
    elapsed = time.time() - cursor.t0
    logger.info("build %s: ok=%s steps=%d elapsed=%.1fs", run_id, cursor.ok, cursor.step, elapsed)
    return BuildResult(bool(cursor.ok), cursor.step, elapsed, cursor.summary)


def _draft_run(request: str) -> Optional[str]:
    """A fresh run with its drafted, unfrozen brief on disk. None when there is no account to own it."""
    users = store.list_users()
    if not users:
        print("no accounts yet — create one first: python -m auth.cli create <handle>")
        return None
    run_id = create_run(users[0].id)
    # The CLI is the employee path — no credit charge, but the compute budget still gates every
    # enqueue, so grant the same seconds a charged build would get or step 1 is refused.
    db_store.charge_game(run_id, 0, SECONDS_PER_CREDIT)
    print(f"run: {run_id}\ndrafting brief for: {request!r}\n")
    spec = draft_spec(request)
    RunState(run_id).write_spec(spec)
    print(json.dumps(spec["design"], indent=2, ensure_ascii=False))
    return run_id


def _cli_draft(request: str) -> int:
    """Stage 1 alone: draft the brief and STOP, leaving spec.json on disk for a human edit."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    run_id = _draft_run(request)
    if run_id is None:
        return 1
    print(f"\nbrief: {RunState(run_id).spec_path.resolve()}")
    print(f"edit it, then: python -m maestro.codegen.run --build {run_id}")
    return 0


def _cli_build(run_id: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    state = RunState(run_id)
    if state.read_spec() is None:
        print(f"no brief for run {run_id!r}")
        return 1
    freeze_spec(run_id)
    print("frozen — building...\n")
    return _report(run_id, state, run_build(run_id))


def _cli(request: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    run_id = _draft_run(request)
    if run_id is None:
        return 1
    print()
    return _cli_build(run_id)


def _report(run_id: str, state: RunState, result: BuildResult) -> int:
    mins, secs = divmod(int(result.elapsed), 60)
    print(f"\nok={result.ok}  steps={result.steps}  elapsed={mins}m{secs:02d}s")
    if result.summary:
        print(f"summary: {result.summary}")
    print(f"game: {game_dir(state.run_dir).resolve()}")
    if result.ok and is_staged(run_id):
        print(f"play: runtime/games/{run_id}/index.html")
    return 0 if result.ok else 1


def _cli_fix(run_id: str, note: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    print(f"fixing {run_id} from note: {note!r}\n")
    return _report(run_id, RunState(run_id), fix_from_note(run_id, note))


def _await_batch(batch_id: str, run_id: str) -> list:
    """Poll a batch to completion and report what landed. CLI-only — a CLI has no socket to report on.

    The chain is advanced by the control plane's /worker/complete, so this needs the API server up."""
    from maestro.codegen.assets import asset_path
    seen = 0
    while True:
        jobs = db_store.batch_jobs(batch_id)
        if len(jobs) > seen:
            seen = len(jobs)
            print(f"  {seen} job(s) queued, "
                  f"{sum(1 for j in jobs if j['status'] in ('done', 'failed'))} done")
        if jobs and all(j["status"] in ("done", "failed") for j in jobs):
            break
        time.sleep(2.0)
    out = []
    for j in jobs:
        aid = j["metadata"].get("asset_id")
        ext = "glb" if j["metadata"].get("kind") == "mesh" else "png"
        if aid and asset_path(run_id, aid, ext).exists():
            out.append(aid)
    return sorted(set(out))


def _cli_assets(run_id: str) -> int:
    from maestro.codegen.assets import add_assets
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    print(f"rendering the assets {run_id} asked for\n")
    out = add_assets(run_id)
    if not out["ok"]:
        print(out.get("error") or "nothing to render")
        return 1
    got = _await_batch(out["batch_id"], run_id)
    print(f"\nplanned={out['planned']}  rendered={len(got)} {got}")
    if not got:
        print("  (nothing rendered — is ComfyUI up? the manifest still stands; re-run to fill)")
    return 0


def _cli_audit(run_id: str) -> int:
    from maestro.codegen.audit import claims_of
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    verdicts = Path(RunState(run_id).run_dir) / "audit_verdicts.jsonl"
    if not verdicts.exists():
        claims = claims_of(RunState(run_id).read_spec())
        print(f"no audit on disk for {run_id!r} ({len(claims)} claim(s) in the brief)")
        return 1
    for line in verdicts.read_text(encoding="utf-8").splitlines():
        for v in json.loads(line).get("verdicts", []):
            print(f"  [{v['status']:>9}] {v['claim']}")
            if v.get("evidence"):
                print(f"              {v['evidence']}")
    return 0


_HELP = """maestro — draft a brief, build a game, render its art.

usage:
  python -m maestro.codegen.run "<request>"   draft → freeze → build → play
  python -m maestro.codegen.run --draft "<request>"       draft the brief and stop (edit spec.json)
  python -m maestro.codegen.run --build <run_id>          freeze the brief on disk → build
  python -m maestro.codegen.run --fix <run_id> "<note>"   apply a human-note fix to a built run
  python -m maestro.codegen.run --audit <run_id>          print the brief-vs-code audit verdicts
  python -m maestro.codegen.run --assets <run_id>         render the art the game declared
  python -m maestro.codegen.run --help | -h              show this help
"""


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] in ("--help", "-h"):
        print(_HELP)
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--draft":
        if len(sys.argv) < 3:
            sys.exit('usage: python -m maestro.codegen.run --draft "<request>"')
        sys.exit(_cli_draft(" ".join(sys.argv[2:])))
    if len(sys.argv) >= 2 and sys.argv[1] == "--build":
        if len(sys.argv) < 3:
            sys.exit("usage: python -m maestro.codegen.run --build <run_id>")
        sys.exit(_cli_build(sys.argv[2]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--fix":
        if len(sys.argv) < 4:
            sys.exit('usage: python -m maestro.codegen.run --fix <run_id> "<what is wrong>"')
        sys.exit(_cli_fix(sys.argv[2], " ".join(sys.argv[3:])))
    if len(sys.argv) >= 2 and sys.argv[1] == "--audit":
        if len(sys.argv) < 3:
            sys.exit("usage: python -m maestro.codegen.run --audit <run_id>")
        sys.exit(_cli_audit(sys.argv[2]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--assets":
        if len(sys.argv) < 3:
            sys.exit('usage: python -m maestro.codegen.run --assets <run_id>')
        sys.exit(_cli_assets(sys.argv[2]))
    if len(sys.argv) < 2:
        sys.exit('usage: python -m maestro.codegen.run "<request>"   |   --fix <run_id> "<note>"')
    sys.exit(_cli(" ".join(sys.argv[1:])))
