"""Spec tools — rare, human-gated. The fence that separates spec from build.

- propose_spec drafts a spec from the request and returns it for human review; it
  does NOT enter the build (frozen stays false).
- amend_spec is the only path to changing a spec mid-build. `reason` is mandatory.
  It always pauses for the human: applying an amendment un-freezes the spec, so the
  build tools refuse until the human re-freezes (the approval action).
- freeze_spec is the human's out-of-band approval — there is deliberately no freeze
  tool the agent can call.

Check-in events are emitted on the event bus so the UI can surface them.
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List

from maestro.state import RunState

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Spec normalizers: a genre layer registers here to enforce a baseline contract (e.g. renpy
# guarantees its structure/quality done-conditions exist regardless of what the proposer
# emitted). Applied at propose AND freeze so the human reviews — and freezes — the real
# contract, not a thin one the LLM happened to draft. Mirrors validate.register_check, so
# maestro stays genre-agnostic.
_SPEC_NORMALIZERS: List[Callable[[Dict], Dict]] = []


def register_spec_normalizer(fn: Callable[[Dict], Dict]) -> None:
    if fn not in _SPEC_NORMALIZERS:
        _SPEC_NORMALIZERS.append(fn)


def _normalize_spec(spec: Dict) -> Dict:
    for fn in _SPEC_NORMALIZERS:
        fn(spec)
    return spec


def _emit(event_type: str, run_id: str, **payload) -> None:
    try:
        from api.websocket.event_bus import event_bus
        from config.time_utils import get_utc_timestamp
        event_bus.publish_sync({"type": event_type, "run_id": run_id,
                                "timestamp": get_utc_timestamp(), **payload})
    except Exception:
        # The event bus is optional (e.g. CLI / tests) — never let it break the build.
        logger.debug("event bus unavailable for %s", event_type)


# Strong textual signals a request wants a point-and-click adventure rather than a VN —
# matched first so the common case skips the extra classification call.
_PNC_KEYWORDS = ("point-and-click", "point and click", "point'n'click", "pointandclick",
                 "point & click", "adventure game", "escape room", "escape-room",
                 "room escape", "hidden object", "inventory puzzle")

_GENRES = ("vn", "point_and_click")


def _classify_genre(request: str) -> str:
    """Pick the genre a request wants. Keyword match first (cheap, deterministic); only an
    ambiguous request costs a classification call. Any failure falls back to 'vn' — the
    human freeze gate catches a wrong guess."""
    low = request.lower()
    if any(k in low for k in _PNC_KEYWORDS):
        return "point_and_click"
    try:
        from renpy.templating import render_template
        from llm_clients.inference import PipelineAgent, JSON_SYSTEM, json_with_correction
        prompt = render_template(_PROMPTS_DIR / "classify_genre.txt", {"request": request})
        agent = PipelineAgent(JSON_SYSTEM, max_tokens=200)
        result = json_with_correction(agent, prompt, "classify_genre", attempts=2) or {}
        genre = result.get("genre")
        return genre if genre in _GENRES else "vn"
    except Exception:
        logger.warning("genre classification failed; defaulting to vn", exc_info=True)
        return "vn"


def propose_spec(request: str, run_id: str) -> Dict:
    """Draft a spec for the request and persist it (unfrozen). Returns the spec."""
    from renpy.templating import render_template
    from llm_clients.inference import PipelineAgent, JSON_SYSTEM, json_with_correction

    genre = _classify_genre(request)
    template = "propose_spec_pnc.txt" if genre == "point_and_click" else "propose_spec.txt"
    prompt = render_template(_PROMPTS_DIR / template, {"request": request})
    agent = PipelineAgent(JSON_SYSTEM, max_tokens=8000)
    spec = json_with_correction(agent, prompt, "propose_spec", attempts=3)

    spec["frozen"] = False
    spec["genre"] = genre  # before _normalize_spec — the baseline picks its checks by genre
    spec.setdefault("request", request)
    _normalize_spec(spec)  # baseline done-conditions in, so the human reviews the real contract
    state = RunState.for_run(run_id)
    state.write_spec(spec)
    _emit("spec_proposed", run_id, title=spec.get("title", ""), genre=genre)
    return spec


def amend_spec(run_id: str, changes: Dict, reason: str) -> Dict:
    """Apply a change to the spec and pause for human re-approval (un-freezes)."""
    if not reason or not reason.strip():
        raise ValueError("amend_spec requires a reason")

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no spec for run {run_id!r}")

    _apply_changes(spec, changes)
    spec["frozen"] = False  # the pause: build refuses until the human re-freezes
    state.write_spec(spec)
    _emit("spec_amend_requested", run_id, reason=reason, changes=changes)
    return {"ok": True, "status": "pending_human_approval", "reason": reason}


def freeze_spec(run_id: str) -> Dict:
    """The human's approval action. Not a tool — called out-of-band (API/CLI)."""
    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no spec for run {run_id!r}")
    _normalize_spec(spec)  # re-assert the baseline in case an amend dropped/lowered a check
    spec["frozen"] = True
    state.write_spec(spec)
    _emit("spec_frozen", run_id, title=spec.get("title", ""))
    return {"ok": True, "frozen": True}


def _apply_changes(spec: Dict, changes: Dict) -> None:
    """Shallow merge for top-level fields; replace a component by id under 'components'."""
    for key, value in changes.items():
        if key == "components" and isinstance(value, list):
            by_id = {c.get("id"): c for c in spec.get("components", [])}
            for comp in value:
                by_id[comp.get("id")] = comp
            spec["components"] = list(by_id.values())
        else:
            spec[key] = value
