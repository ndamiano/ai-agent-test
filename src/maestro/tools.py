"""Artifact tools — the frequent, autonomous capabilities the executor dispatches.

Bound to one run's durable state. build_tools(spec, state) returns the {name: fn}
registry the Executor consumes; the agent chooses which to call and when.

The build tools (write_component, generate_asset) refuse to run until the spec is
frozen — the human gate is load-bearing. read_component, validate, compile_renpy
and update_scratchpad are safe before freezing.

State is bounded on purpose: there is no raw read_file/write_file. Components are
written by id; scratchpad is replaced, not appended.
"""

from typing import Callable, Dict, List, Optional

from maestro.validate import validate


class SpecNotFrozen(RuntimeError):
    pass


def build_tools(spec, state) -> Dict[str, Callable]:
    def _require_frozen():
        if not spec.frozen:
            raise SpecNotFrozen("spec must be frozen before building the artifact")

    # ── artifact mutation (gated on freeze) ──────────────────────────────────
    def write_component(component_id: str, content) -> Dict:
        _require_frozen()
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}

    def generate_asset() -> Dict:
        """Generate the image assets the asset_manifest declares (wraps comfyui)."""
        _require_frozen()
        from renpy.fns import generate_images
        result = generate_images(state.load_artifact(), state.run_dir)
        return {"ok": result.get("status") == "ok", **result}

    # ── inspection (safe pre-freeze) ─────────────────────────────────────────
    def read_component(component_id: str) -> Dict:
        content = state.read_component(component_id)
        if content is None:
            return {"ok": False, "error": f"no component {component_id!r}"}
        return {"ok": True, "component_id": component_id, "content": content}

    def validate_tool(component_id: Optional[str] = None) -> Dict:
        failures = validate(spec, state, component_id)
        return {"ok": not failures, "failures": failures}

    def compile_renpy_tool() -> Dict:
        from renpy.compiler import compile_renpy
        return compile_renpy(state.run_dir)

    # ── working memory ───────────────────────────────────────────────────────
    def update_scratchpad(current_goal: str = "",
                          recent_decisions: Optional[List[str]] = None,
                          open_questions: Optional[List[str]] = None) -> Dict:
        state.write_scratchpad(current_goal, recent_decisions, open_questions)
        return {"ok": True}

    def request_review(question: str, options: Optional[List[str]] = None) -> Dict:
        # Scoped escape hatch. Phase 5 surfaces this to the human over the event
        # bus; here it just returns a structured pending marker.
        return {"ok": True, "status": "review_requested",
                "question": question, "options": options or []}

    return {
        "write_component": write_component,
        "read_component": read_component,
        "generate_asset": generate_asset,
        "validate": validate_tool,
        "compile_renpy": compile_renpy_tool,
        "update_scratchpad": update_scratchpad,
        "request_review": request_review,
    }
