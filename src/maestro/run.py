"""Run orchestrator + validation CLI.

  create_run()              → fresh run_id with an empty run dir
  run_build(run_id)         → drive the executor (LLM agent + tools) to completion
  python -m maestro.run "<request>"  → propose a spec, freeze it (with your ok),
                                       build, and print the launchable project path

The build refuses unless the spec is frozen — freezing is your action.
"""

import logging
import sys
import time
import uuid

from maestro.spec import Spec

logger = logging.getLogger(__name__)
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.agent_loop import AgentLoop, LoopResult


def create_run() -> str:
    run_id = uuid.uuid4().hex[:12]
    RunState.for_run(run_id)  # creates the dir
    return run_id


def run_build(run_id: str, max_steps: int = 300) -> LoopResult:
    # A spec that demands a real VN (many nodes + branching) needs more steps than a trivial one;
    # the loop is cheap (compile checks lint-only, steps run <15s), so budget for a 50-node game.
    from tools.spec_tools import _emit
    from maestro.modules import compose
    from maestro.run_control import get_or_create, remove
    from maestro.call_log import LoggingConnector
    from llm_clients.connector_selector import get_connector

    state = RunState.for_run(run_id)
    spec_data = state.read_spec()
    if spec_data is None:
        raise ValueError(f"no spec for run {run_id!r} — propose one first")
    modules = compose(spec_data.get("modules", []))   # human auto-included
    tools = build_tools(spec_data, state, modules)
    control = get_or_create(run_id)
    conn = LoggingConnector(get_connector(), state.run_dir / "llm_calls")
    from config.settings_manager import settings_manager
    loop = AgentLoop(
        spec_data, state, modules, tools, connector=conn, max_steps=max_steps,
        on_event=lambda ev: _emit(ev.pop("type"), run_id, **ev),
        on_milestone=lambda cid: _emit("component_complete", run_id, component_id=cid),
        control=control,
        parallel=settings_manager.get_settings().get("parallel_fixes", 1),
    )

    t0 = time.perf_counter()
    try:
        result = loop.run()
    finally:
        remove(run_id)

    if result.ok:
        # Generate real art (ComfyUI when up, placeholder fallback) then package once. The loop's
        # compile checks are lint-only and the agent may never call generate_asset, so finalize here.
        from renpy.fns import generate_images, generate_voices
        from maestro.engines import compile_for
        artifact = state.load_artifact()
        try:
            generate_images(artifact, state.run_dir)
        except Exception:
            pass  # placeholders already cover the build; never fail delivery on art
        try:
            generate_voices(artifact, state.run_dir)
        except Exception:
            pass  # silent placeholders cover the script; never fail delivery on voice
        compile_for(spec_data.get("engine", "renpy"))(state.run_dir, distribute=True)

    result.elapsed = time.perf_counter() - t0
    logger.info("build %s: ok=%s steps=%d elapsed=%.1fs",
                run_id, result.ok, result.steps, result.elapsed)
    return result


def rewrite_node_run(run_id: str, node_id: str, note: str) -> dict:
    """Regenerate one node from a human note, on its own (outside the build loop). Emits
    node_rewrite_started/done so the panel can react, and re-compiles so the output reflects
    the change. Meant to run on a background thread, like run_build."""
    from tools.spec_tools import _emit
    from maestro.modules import compose
    from maestro.rewrite import rewrite_node
    from maestro.engines import compile_for
    from maestro.call_log import LoggingConnector
    from llm_clients.connector_selector import get_connector

    state = RunState.for_run(run_id)
    spec_data = state.read_spec()
    modules = compose(spec_data.get("modules", []))
    tools = build_tools(spec_data, state, modules)
    conn = LoggingConnector(get_connector(), state.run_dir / "llm_calls")
    _emit("node_rewrite_started", run_id, node_id=node_id, note=note)
    result = rewrite_node(spec_data, state, node_id, note, tools, connector=conn,
                          report=lambda m: _emit("node_rewrite_step", run_id, node_id=node_id, summary=m))
    if result.get("ok"):
        try:
            compile_for(spec_data.get("engine", "renpy"))(state.run_dir, distribute=False)
        except Exception:
            logger.exception("recompile after rewrite failed for %s", run_id)
    _emit("node_rewrite_done", run_id, node_id=node_id, ok=bool(result.get("ok")),
          error=result.get("error"))
    return result


def _cli(request: str, *, yes: bool = False) -> int:
    from tools.spec_tools import propose_spec, freeze_spec
    import json

    # Without this the whole maestro/llm_clients/renpy log tree is silent on the CLI
    # path — a 100-step build would emit nothing. INFO surfaces per-call + per-decision.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

    run_id = create_run()
    print(f"run: {run_id}\nproposing spec for: {request!r}\n")
    spec = propose_spec(request, run_id)
    print(json.dumps(spec, indent=2, ensure_ascii=False))

    if not yes:
        answer = input("\nFreeze this spec and build? [y/N] ").strip().lower()
        if answer != "y":
            print("Not frozen. Edit the spec and re-run, or freeze later.")
            return 0

    freeze_spec(run_id)
    print("\nfrozen — building...\n")
    result = run_build(run_id)

    mins, secs = divmod(int(result.elapsed), 60)
    print(f"\nok={result.ok}  steps={result.steps}  elapsed={mins}m{secs:02d}s "
          f"({result.elapsed:.1f}s)")
    if not result.ok:
        for e in result.failures:
            print(f"  unmet: [{e.component}] {e.code}: {e.message}")
    project = (RunState.for_run(run_id).run_dir / "game_output").resolve()
    print(f"project: {project}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--yes"]
    if not args:
        sys.exit('usage: python -m maestro.run [--yes] "<request>"')
    sys.exit(_cli(" ".join(args), yes="--yes" in sys.argv[1:]))
