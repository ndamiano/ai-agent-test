"""Codegen run orchestrator + CLI.

  create_run(user_id)          → a fresh run dir
  draft_spec(request)          → the local model drafts a design SPEC (stage 1)
  run_build(run_id)            → the AgentLoop drives CodegenModule to a passing game.js (stage 2)
  python -m maestro.codegen.run "<request>"  → draft → freeze (your ok) → build → play path

The build refuses until the spec is frozen. One game per run: runs/<id>/game.js.
"""

import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path

from auth import store
from db import store as db_store
from llm_clients.connector import get_connector
from llm_clients.message_builder import MessageBuilder
from maestro.agent_loop import AgentLoop
from maestro.codegen import worldgen_bridge
from maestro.codegen.controls import normalize_controls
from maestro.codegen.fix_classes import classify
from maestro.codegen.gates import RUNTIME_DIR, entry_src_path, game_dir, stage_for_play
from maestro.codegen.module import (
    _FIX_LOOP_MAX_TURNS,
    CodegenModule,
    _read_write_loop_fix,
)
from maestro.codegen.scaffold import seed_scaffold
from maestro.codegen.tools import build_codegen_tools
from maestro.modules.context import build_context
from maestro.modules.module import Error, ErrorType
from maestro.run_control import get_or_create, remove
from maestro.services import BudgetExhausted, Services
from maestro.state import RunState
from tools.build_events import _emit
from tools.execution_context import run_scope

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"


def _seed(run_id: str, state: RunState, spec: dict) -> None:
    """The pre-seeds the build loop authors on top of, each written only when absent so a rebuild,
    reskin or note-fix never regenerates one under a half-built game. Two independent axes:

      world.ts  CONTENT — worldgen owns the PLACE (terrain, town, roads, POIs). World specs only.
      main.ts   CONTROL — the scaffold owns the frozen spec's control scheme. EVERY game.

    Both are `// GENERATED` (the edit tool refuses them); the model authors game.ts against both.
    """
    gd = game_dir(state.run_dir)
    gd.mkdir(parents=True, exist_ok=True)
    if spec.get("world") and not (gd / "world.ts").exists():
        info = worldgen_bridge.seed_world(gd, spec, run_id)
        logger.info("worldgen seed %s: %sx%s town, %d buildings",
                    run_id, info["gw"], info["gh"], len(info["buildings"]))
    if not entry_src_path(state.run_dir).exists():
        seed_scaffold(state, spec)


def create_run(user_id: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    RunState(run_id)
    db_store.create_game(run_id, user_id)
    return run_id


def _content(resp) -> str:
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def draft_spec(request: str) -> dict:
    """Stage 1: prose request → design SPEC JSON. Prose-in, JSON-out — the easy half. Retries on a
    bad JSON parse (local models occasionally emit a trailing comma / stray token)."""

    conn = get_connector()
    catalog = (RUNTIME_DIR / "kit_catalog.md").read_text(encoding="utf-8")
    system = (_PROMPTS / "spec_draft.txt").read_text(encoding="utf-8").replace("{catalog}", catalog)
    user = f"Request: {request}\n\nWrite the JSON spec."
    design, last = None, ""
    for attempt in range(3):
        reply = _content(conn.generate_with_tools(
            MessageBuilder(system).add_user(user).build(), [], max_tokens=4000))
        m = re.search(r"```(?:json)?\s*\n(.*?)```", reply, re.S)
        try:
            design = json.loads(m.group(1) if m else reply)
            break
        except json.JSONDecodeError as e:
            last = f"{e} — return ONLY one ```json block of STRICT valid JSON, no trailing commas."
            user = f"Request: {request}\n\nYour previous JSON was invalid: {last}\n\nWrite the JSON spec."
    if design is None:
        raise ValueError(f"spec draft never produced valid JSON: {last}")
    normalize_controls(design)
    return {"request": request, "title": design.get("title", request),
            "mode": design.get("mode", "2d"), "world": design.get("world"),
            "design": design, "frozen": False}


def propose_spec(request: str, run_id: str) -> dict:
    """Draft a spec from the request, persist it to the run, and announce it for human review."""

    spec = draft_spec(request)
    RunState(run_id).write_spec(spec)
    _mirror_spec_meta(run_id, spec)
    _emit("spec_proposed", run_id, title=spec["title"], mode=spec["mode"])
    return spec


def amend_spec(run_id: str, note: str) -> dict:
    """Re-draft an existing spec's design from a free-text revision note. Writes it UNFROZEN so the
    build refuses until the human re-freezes."""

    state = RunState(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")
    augmented = (f"Original request: {spec['request']}\n"
                 f"Revision requested: {note}\n"
                 f"Current design JSON: {json.dumps(spec['design'], ensure_ascii=False)}\n"
                 "Produce the full updated design.")
    revised = draft_spec(augmented)
    revised["request"] = spec["request"]
    revised["frozen"] = False
    state.write_spec(revised)
    _mirror_spec_meta(run_id, revised)
    _emit("spec_amend_requested", run_id, note=note)
    return revised


def freeze_spec(run_id: str) -> dict:
    """The human's out-of-band approval: freeze the spec so the build may run."""

    state = RunState(run_id)
    spec = state.read_spec()
    spec["frozen"] = True
    normalize_controls(spec.get("design") or {})   # also catches a hand-written / hand-edited spec
    state.write_spec(spec)
    _mirror_spec_meta(run_id, spec)
    _emit("spec_frozen", run_id, title=spec["title"])
    return {"ok": True, "frozen": True}


def _mirror_spec_meta(run_id: str, spec: dict) -> None:
    """spec.json is the source of truth; the games row mirrors its identity fields for listing."""
    db_store.update_spec_meta(run_id, spec.get("title", ""), spec.get("mode", ""),
                              bool(spec.get("frozen")))


def run_build(run_id: str, max_steps: int = 60):
    """Stage 2: drive CodegenModule until game.js passes the local gates. The surviving AgentLoop
    does the driving — collect errors (authored/runs/plays), fix the top one, repeat."""

    state = RunState(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no spec for run {run_id!r} — draft one first")

    _seed(run_id, state, spec)
    tools = build_codegen_tools(state)
    control = get_or_create(run_id)
    loop = AgentLoop(spec, state, [CodegenModule()], tools, connector=get_connector(),
                     max_steps=max_steps, control=control,
                     on_event=lambda ev: _emit(ev.pop("type"), run_id, **ev),
                     on_milestone=lambda cid: _emit("component_complete", run_id, component_id=cid))
    db_store.set_status(run_id, "building")
    t0 = time.perf_counter()
    try:
        with run_scope(run_id):
            result = loop.run()
    except BaseException:
        db_store.set_status(run_id, "failed")
        raise
    finally:
        remove(run_id)
    result.elapsed = time.perf_counter() - t0
    if result.ok:
        stage_for_play(state.run_dir, run_id)
    db_store.set_status(run_id, "built" if result.ok else "failed")
    logger.info("codegen build %s: ok=%s steps=%d elapsed=%.1fs",
                run_id, result.ok, result.steps, result.elapsed)
    return result


def fix_from_note(run_id: str, note: str, max_steps: int = 40):
    """Patch a built game from a HUMAN playtest note (the local play-critic: the human is the eye the
    headless gates aren't). The note runs through the SAME read→edit subloop as a gate failure — the
    note is the failing-gate text, a synthetic Error whose neutral code ("human") classifies to the
    `default` fix class — so a human fix gets grounded hunk edits, never a whole-file rewrite. Then
    re-run the loop so any gate the patch regresses is re-fixed before shipping."""

    state = RunState(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")
    error = Error(type=ErrorType.HUMAN, code="human", component="game",
                  message=("HUMAN PLAYTEST FEEDBACK — the game passed the automated gates but is "
                           f"WRONG when a person plays it. Fix exactly this:\n{note}"))
    services = Services(get_connector(), build_codegen_tools(state), spec, state,
                        budget=_FIX_LOOP_MAX_TURNS)
    try:
        with run_scope(run_id):
            _read_write_loop_fix(None, build_context(spec, state), error, 0, services,
                                 services.dispatch, fix_class=classify(error))
    except BudgetExhausted:
        pass   # the subloop spent its cap — the re-gate below still runs and re-fixes
    return run_build(run_id, max_steps=max_steps)   # re-gate (stages on ok) + auto-fix any regression the patch caused


def _cli(request: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

    # TODO Think through this. The CLI is only used by employees.
    # We should probably require an account
    users = store.list_users()
    if not users:
        print("no accounts yet — create one first: python -m auth.cli create <handle>")
        return 1
    owner = users[0].id

    run_id = create_run(owner)
    print(f"run: {run_id}\ndrafting spec for: {request!r}\n")
    spec = draft_spec(request)
    state = RunState(run_id)
    state.write_spec(spec)
    print(json.dumps(spec["design"], indent=2, ensure_ascii=False))

    freeze_spec(run_id)
    print("\nfrozen — building...\n")
    result = run_build(run_id)

    mins, secs = divmod(int(result.elapsed), 60)
    print(f"\nok={result.ok}  steps={result.steps}  elapsed={mins}m{secs:02d}s")
    if not result.ok:
        for e in result.failures:
            print(f"  unmet: [{e.component}] {e.code}: {e.message[:200]}")
    print(f"game: {entry_src_path(state.run_dir).resolve()}")
    if result.ok:
        print(f"play: runtime/index.html?game={run_id}")
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


def _cli_assets(run_id: str) -> int:
    # Lazy on purpose: reskin imports run_build from here. One side of the cycle must stay
    # deferred, and this CLI entry is the cheaper side to defer.
    from maestro.codegen.reskin import add_assets  # noqa: PLC0415
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    print(f"skinning {run_id} with generated assets\n")
    out = add_assets(run_id)
    got = out["generated"]
    planned = out.get("meshes") if out["mode"] == "3d" else out.get("sprites")
    kind = "meshes" if out["mode"] == "3d" else "sprites"
    print(f"\nok={out['ok']}  mode={out['mode']}  {kind}={len(planned)}  rendered={len(got)} {got}")
    if not got:
        backend = "ComfyUI + TRELLIS" if out["mode"] == "3d" else "ComfyUI"
        print(f"  (nothing rendered — is {backend} up? tags + manifest still landed; re-run to fill)")
    if out["ok"]:
        print(f"play: runtime/index.html?game={run_id}")
    return 0 if out["ok"] else 1


_HELP = """maestro codegen — draft a spec, build a game, skin it with assets.

usage:
  python -m maestro.codegen.run "<request>"   draft → freeze → build → play
  python -m maestro.codegen.run --fix <run_id> "<note>"   apply a human-note fix to a built run
  python -m maestro.codegen.run --assets <run_id>         run the asset (reskin) stage on a built run
  python -m maestro.codegen.run --help | -h              show this help
"""


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] in ("--help", "-h"):
        print(_HELP)
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "--fix":
        if len(sys.argv) < 4:
            sys.exit('usage: python -m maestro.codegen.run --fix <run_id> "<what is wrong>"')
        sys.exit(_cli_fix(sys.argv[2], " ".join(sys.argv[3:])))
    if len(sys.argv) >= 2 and sys.argv[1] == "--assets":
        if len(sys.argv) < 3:
            sys.exit('usage: python -m maestro.codegen.run --assets <run_id>')
        sys.exit(_cli_assets(sys.argv[2]))
    if len(sys.argv) < 2:
        sys.exit('usage: python -m maestro.codegen.run "<request>"   |   --fix <run_id> "<note>"')
    sys.exit(_cli(" ".join(sys.argv[1:])))
