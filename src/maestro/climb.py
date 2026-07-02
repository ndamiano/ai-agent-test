"""Re-run ONE module against a finished run — the fixture for prompt hill-climbing.

Clones the source run into a fresh run dir (same upstream components, same frozen
spec), wipes the target module's own component, and drives the loop with only that
module composed. Same inputs + a candidate prompt = a comparable output.

usage (from src/):
  python -m maestro.climb <src_run_id> <module_id> [--label tag] [--max-steps N] [--keep]

  --keep      don't wipe the module's component (fix-mode: loop only repairs errors)
  --label     human tag baked into the new run id (default: the module id)
"""

import argparse
import json
import logging
import shutil
import sys
import time
import uuid

from maestro.state import RunState, SPEC_FILE, STORY_STATE_FILE
from maestro.story_state import init_story_state

logger = logging.getLogger(__name__)

_SKIP = {"scratchpad.json", "human_todos.json", "waivers.json", STORY_STATE_FILE}


def clone_run(src_run_id: str, label: str) -> str:
    src = RunState.for_run(src_run_id).run_dir
    if not (src / SPEC_FILE).exists():
        raise ValueError(f"no spec in source run {src_run_id!r}")
    new_id = f"{label}-{uuid.uuid4().hex[:8]}"
    dst = RunState.for_run(new_id).run_dir
    for path in src.glob("*.json"):
        if path.name in _SKIP:
            continue
        shutil.copy(path, dst / path.name)
    spec = json.loads((dst / SPEC_FILE).read_text())
    state = RunState.for_run(new_id)
    state.write_story_state(init_story_state(spec.get("story_state_schema")))
    return new_id


def run_module(run_id: str, module_id: str, *, wipe: bool = True, max_steps: int = 300):
    from maestro.modules import compose
    from maestro.tools import build_tools
    from maestro.agent_loop import AgentLoop
    from maestro.call_log import LoggingConnector
    from llm_clients.connector_selector import get_connector
    from config.settings_manager import settings_manager

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    modules = [m for m in compose(spec.get("modules", [])) if m.id == module_id]
    if not modules:
        raise ValueError(f"module {module_id!r} not in spec set {spec.get('modules')}")

    if wipe:
        for cid in modules[0].affected_components():
            path = state.run_dir / f"{cid}.json"
            if path.exists():
                path.unlink()

    tools = build_tools(spec, state, modules)
    conn = LoggingConnector(get_connector(), state.run_dir / "llm_calls")
    loop = AgentLoop(spec, state, modules, tools, connector=conn, max_steps=max_steps,
                     parallel=settings_manager.get_settings().get("parallel_fixes", 1))
    t0 = time.perf_counter()
    result = loop.run()
    result.elapsed = time.perf_counter() - t0
    return result


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src_run_id")
    parser.add_argument("module_id")
    parser.add_argument("--label", default=None)
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
    run_id = clone_run(args.src_run_id, args.label or args.module_id)
    print(f"cloned {args.src_run_id} -> {run_id}", flush=True)
    result = run_module(run_id, args.module_id, wipe=not args.keep,
                        max_steps=args.max_steps)
    mins, secs = divmod(int(result.elapsed), 60)
    print(f"\nok={result.ok}  steps={result.steps}  elapsed={mins}m{secs:02d}s")
    for e in result.failures:
        print(f"  unmet: [{e.component}] {e.code}: {e.message}")
    print(f"run dir: {RunState.for_run(run_id).run_dir}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(_cli())
