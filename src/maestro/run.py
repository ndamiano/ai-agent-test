"""Run orchestrator + validation CLI.

  create_run()              → fresh run_id with an empty run dir
  run_build(run_id)         → drive the executor (LLM agent + tools) to completion
  python -m maestro.run "<request>"  → propose a spec, freeze it (with your ok),
                                       build, and print the launchable project path

The build refuses unless the spec is frozen — freezing is your action.
"""

import sys
import uuid
from typing import Optional

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.agent import make_llm_decider
from maestro.executor import Executor, ExecutorResult


def create_run() -> str:
    run_id = uuid.uuid4().hex[:12]
    RunState.for_run(run_id)  # creates the dir
    return run_id


def run_build(run_id: str, max_steps: int = 60, decide=None) -> ExecutorResult:
    from maestro.spec_tools import _emit

    state = RunState.for_run(run_id)
    spec_data = state.read_spec()
    if spec_data is None:
        raise ValueError(f"no spec for run {run_id!r} — propose one first")
    spec = Spec(spec_data)

    from renpy.component_schemas import SCHEMAS as renpy_schemas, skeleton_guide

    tools = build_tools(spec, state, schemas=renpy_schemas)
    decider = decide or make_llm_decider(component_guide=skeleton_guide())
    executor = Executor(
        spec, state, tools, decider, max_steps=max_steps,
        on_milestone=lambda cid: _emit("component_complete", run_id, component_id=cid),
    )
    result = executor.run()

    # The loop's compile checks are lint-only; package the project once at the end.
    if result.ok:
        from renpy.compiler import compile_renpy
        compile_renpy(state.run_dir, distribute=True)
    return result


def _cli(request: str) -> int:
    from maestro.spec_tools import propose_spec, freeze_spec
    import json

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

    print(f"\nok={result.ok}  steps={result.steps}")
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
