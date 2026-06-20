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
from maestro.tools import build_tools, tool_schemas_for
from maestro.agent import make_llm_decider, make_node_subloop
from maestro.executor import Executor, ExecutorResult


def create_run() -> str:
    run_id = uuid.uuid4().hex[:12]
    RunState.for_run(run_id)  # creates the dir
    return run_id


def run_build(run_id: str, max_steps: int = 300, decide=None) -> ExecutorResult:
    # A spec that demands a real VN (many nodes + branching) needs more steps than a
    # trivial one; the loop is cheap now (compile checks lint-only, steps run <15s), so
    # budget for a 50-node game with wiring + compile-fix slack rather than starving it.
    from maestro.spec_tools import _emit
    from maestro.agent import make_place_subloop
    from renpy.ir_checks import register_all as register_ir_checks, node_view, place_view
    from renpy.component_schemas import SCHEMAS as ir_schemas, skeleton_guide

    register_ir_checks()  # make reachable_from_start / places_reachable / ... available

    state = RunState.for_run(run_id)
    spec_data = state.read_spec()
    if spec_data is None:
        raise ValueError(f"no spec for run {run_id!r} — propose one first")
    spec = Spec(spec_data)
    guide = skeleton_guide(genre=spec.genre)

    tools = build_tools(spec, state, schemas=ir_schemas)
    decider = decide or make_llm_decider(component_guide=guide,
                                         tool_schemas=tool_schemas_for(spec.genre))
    # nodes / places iterate until each target check passes — a stateful sub-loop, not one-shot
    # steps. A scripted `decide` (tests) keeps the simple stateless path. A point-and-click game
    # drives places AND reuses nodes for NPC dialogue.
    sub_runners = {}
    projectors = {"nodes": node_view}
    if not decide:
        sub_runners["nodes"] = make_node_subloop(component_guide=guide)
    if spec.genre == "point_and_click":
        projectors["places"] = place_view
        if not decide:
            sub_runners["places"] = make_place_subloop(component_guide=guide)
    executor = Executor(
        spec, state, tools, decider, max_steps=max_steps,
        on_milestone=lambda cid: _emit("component_complete", run_id, component_id=cid),
        on_event=lambda ev: _emit(ev.pop("type"), run_id, **ev),
        projectors=projectors,
        sub_runners=sub_runners,
    )

    t0 = time.perf_counter()
    result = executor.run()

    if result.ok:
        # Generate real art (ComfyUI when up, placeholder fallback) then package once.
        # The loop's compile checks are lint-only and the agent may never call
        # generate_asset, so finalize assets here for delivery.
        from renpy.fns import generate_images
        from renpy.compiler import compile_renpy
        try:
            generate_images(state.load_artifact(), state.run_dir)
        except Exception:
            pass  # placeholders already cover the build; never fail delivery on art
        compile_renpy(state.run_dir, distribute=True)

    result.elapsed = time.perf_counter() - t0
    logger.info("build %s: ok=%s steps=%d elapsed=%.1fs",
                run_id, result.ok, result.steps, result.elapsed)
    return result


def _cli(request: str) -> int:
    from maestro.spec_tools import propose_spec, freeze_spec
    import json

    # Without this the whole maestro/llm_clients/renpy log tree is silent on the CLI
    # path — a 100-step build would emit nothing. INFO surfaces per-call + per-decision.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

    # Register the renpy checks + spec baseline before proposing, so the proposed spec the
    # human reviews already carries the enforced contract (run_build registers too, but that
    # is after freeze).
    from renpy.ir_checks import register_all
    register_all()

    run_id = create_run()
    print(f"run: {run_id}\nproposing spec for: {request!r}\n")
    spec = propose_spec(request, run_id)
    print(json.dumps(spec, indent=2, ensure_ascii=False))

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
        for f in result.failures:
            print(f"  unmet: [{f['component_id']}] {f['check'].get('type')}: {f.get('detail')}")
    project = (RunState.for_run(run_id).run_dir / "game_output").resolve()
    print(f"project: {project}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python -m maestro.run "<request>"')
    sys.exit(_cli(" ".join(sys.argv[1:])))
