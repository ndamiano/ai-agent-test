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
from maestro.templating import render_template

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_WORLDGEN_BIOMES = ["ocean", "beach", "grassland", "forest", "hill", "mountain"]


def _recipe_from_spec(spec: dict) -> dict:
    """The worldgen recipe for a world-flagged spec — the requested settlement / size / biomes."""
    w = spec.get("world") if isinstance(spec.get("world"), dict) else {}
    name = (w.get("settlement") or spec.get("title") or "Village").strip()
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "settlement"
    return {"archetype": "continent", "size": w.get("size") or "small",
            "palette": {"biomes": w.get("biomes") or _WORLDGEN_BIOMES},
            "locations": [{"id": slug, "type": "settlement", "name": name}]}


def _world_main_purpose(spec: dict) -> str:
    """main.ts's manifest purpose for a world game — the fixed scaffold (import world.ts, spawnWorld,
    player-on-ground, drive, dialogue primitives) with the frozen spec's entities + objective inlined,
    so the model realizes THIS game on the village, not a fixed Rivervale."""
    d = spec.get("design", spec)
    ents = d.get("entities") or []
    entities = "; ".join(f"{e.get('id', '?')}: {e.get('desc', '')}" for e in ents) or "the spec's entities"
    scheme = ((d.get("control") or {}).get("scheme") or "").lower()
    return render_template(_PROMPTS / "world_main_purpose.txt", {
        "title": d.get("title") or spec.get("title") or "the game",
        "controls": "fp" if "first-person" in scheme else "orbital",
        "entities": entities,
        "objective": d.get("win") or "explore the village",
        "lose": d.get("lose") or "none",
    })


def _maybe_seed_worldgen(run_id: str, state: RunState, spec: dict) -> None:
    """World-flagged 3D game: worldgen OWNS the layout. Before the build loop, generate world.ts
    (terrain heightfield + labelled building parcels) via the bridge and force a single-file manifest
    (main.ts only) so the planner is bypassed and the model authors gameplay ON TOP. Idempotent — on
    re-entry (re-gate after ok, reskin, note-fix) world.ts already exists and this is a no-op, so the
    town is never regenerated under a half-built game."""
    from maestro.codegen import worldgen_bridge
    from maestro.codegen.gates import game_dir

    if not spec.get("world"):
        return
    gd = game_dir(state.run_dir)
    if (gd / "world.ts").exists():
        return
    gd.mkdir(parents=True, exist_ok=True)
    info = worldgen_bridge.build(_recipe_from_spec(spec), gd)
    logger.info("worldgen seed %s: %sx%s town, %d buildings",
                run_id, info["gw"], info["gh"], len(info["buildings"]))
    manifest = {"files": [{"name": "main.ts", "purpose": _world_main_purpose(spec),
                           "exports": ["createGame"]}]}
    (gd / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def create_run(user_id: str) -> str:
    from db import store as db_store

    run_id = uuid.uuid4().hex[:12]
    RunState.for_run(run_id)   # materialize the run dir
    db_store.create_game(run_id, user_id)
    return run_id


def _content(resp) -> str:
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def draft_spec(request: str) -> dict:
    """Stage 1: prose request → design SPEC JSON. Prose-in, JSON-out — the easy half. Retries on a
    bad JSON parse (local models occasionally emit a trailing comma / stray token)."""
    from llm_clients.connector_selector import get_connector
    from llm_clients.message_builder import MessageBuilder

    from maestro.codegen.gates import RUNTIME_DIR

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
    return {"request": request, "title": design.get("title", request),
            "mode": design.get("mode", "2d"), "world": design.get("world"),
            "design": design, "frozen": False}


def propose_spec(request: str, run_id: str) -> dict:
    """Draft a spec from the request, persist it to the run, and announce it for human review."""
    from tools.build_events import _emit

    spec = draft_spec(request)
    RunState.for_run(run_id).write_spec(spec)
    _mirror_spec_meta(run_id, spec)
    _emit("spec_proposed", run_id, title=spec["title"], mode=spec["mode"])
    return spec


def amend_spec(run_id: str, note: str) -> dict:
    """Re-draft an existing spec's design from a free-text revision note. Writes it UNFROZEN so the
    build refuses until the human re-freezes."""
    from tools.build_events import _emit

    state = RunState.for_run(run_id)
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
    from tools.build_events import _emit

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    spec["frozen"] = True
    state.write_spec(spec)
    _mirror_spec_meta(run_id, spec)
    _emit("spec_frozen", run_id, title=spec["title"])
    return {"ok": True, "frozen": True}


def _mirror_spec_meta(run_id: str, spec: dict) -> None:
    """spec.json is the source of truth; the games row mirrors its identity fields for listing."""
    from db import store as db_store

    db_store.update_spec_meta(run_id, spec.get("title", ""), spec.get("mode", ""),
                              bool(spec.get("frozen")))


def run_build(run_id: str, max_steps: int = 60):
    """Stage 2: drive CodegenModule until game.js passes the local gates. The surviving AgentLoop
    does the driving — collect errors (authored/runs/plays), fix the top one, repeat."""
    from maestro.agent_loop import AgentLoop
    from maestro.codegen.gates import stage_for_play
    from maestro.codegen.module import CodegenModule
    from maestro.codegen.tools import build_codegen_tools
    from maestro.run_control import get_or_create, remove
    from llm_clients.connector_selector import get_connector
    from llm_clients.log_context import set_log_dir
    from tools.build_events import _emit

    from db import store as db_store

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no spec for run {run_id!r} — draft one first")
    set_log_dir(str(state.run_dir / "logs"))
    _maybe_seed_worldgen(run_id, state, spec)
    tools = build_codegen_tools(state)
    control = get_or_create(run_id)
    loop = AgentLoop(spec, state, [CodegenModule()], tools, connector=get_connector(),
                     max_steps=max_steps, control=control,
                     on_event=lambda ev: _emit(ev.pop("type"), run_id, **ev),
                     on_milestone=lambda cid: _emit("component_complete", run_id, component_id=cid))
    db_store.set_status(run_id, "building")
    t0 = time.perf_counter()
    try:
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
    headless gates aren't). One targeted patch from the note, then re-run the loop so any gate the
    patch regresses is re-fixed before shipping."""
    from maestro.codegen.module import _triage_file, _focused_fix
    from maestro.codegen.tools import build_codegen_tools
    from llm_clients.message_builder import MessageBuilder
    from llm_clients.connector_selector import get_connector

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")
    conn = get_connector()

    def infer(system, user, mt):
        msgs = MessageBuilder(system).add_user(user).build()
        return _content(conn.generate_with_tools(msgs, [], max_tokens=mt))

    failure = ("HUMAN PLAYTEST FEEDBACK — the game passed the automated gates but is WRONG when a "
               f"person plays it. Fix exactly this:\n{note}")
    tools = build_codegen_tools(state)
    dispatch = lambda name, args: tools[name](**args)   # _focused_fix calls dispatch(name, args)
    target = _triage_file(infer, state.run_dir, failure, use_stack=False)   # a prose note isn't a stack trace
    _focused_fix(infer, spec, state.run_dir, target, failure, dispatch)
    return run_build(run_id, max_steps=max_steps)   # re-gate (stages on ok) + auto-fix any regression the patch caused


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

    freeze_spec(run_id)
    print("\nfrozen — building...\n")
    result = run_build(run_id)

    mins, secs = divmod(int(result.elapsed), 60)
    print(f"\nok={result.ok}  steps={result.steps}  elapsed={mins}m{secs:02d}s")
    if not result.ok:
        for e in result.failures:
            print(f"  unmet: [{e.component}] {e.code}: {e.message[:200]}")
    state = RunState.for_run(run_id)
    from maestro.codegen.gates import entry_src_path
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
    from maestro.codegen.reskin import add_assets
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
  python -m maestro.codegen.run [--yes] "<request>"   draft → freeze → build → play
  python -m maestro.codegen.run --fix <run_id> "<note>"   apply a human-note fix to a built run
  python -m maestro.codegen.run --assets <run_id>         run the asset (reskin) stage on a built run
  python -m maestro.codegen.run --help | -h              show this help

flags:
  --yes    skip the interactive spec-freeze gate (auto-freeze the drafted spec)
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
    args = [a for a in sys.argv[1:] if a != "--yes"]
    if not args:
        sys.exit('usage: python -m maestro.codegen.run [--yes] "<request>"   |   --fix <run_id> "<note>"')
    sys.exit(_cli(" ".join(args), yes="--yes" in sys.argv[1:]))
