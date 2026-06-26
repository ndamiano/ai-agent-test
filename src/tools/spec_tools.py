"""Spec tools — rare, human-gated. The fence that separates spec from build.

- propose_spec drafts a spec from the request and returns it for human review; it does NOT enter
  the build (frozen stays false).
- amend_spec is the only path to changing a spec mid-build. `reason` is mandatory. It always pauses
  for the human: applying an amendment un-freezes the spec, so the build tools refuse until the
  human re-freezes (the approval action).
- freeze_spec is the human's out-of-band approval — there is deliberately no freeze tool.

The proposer writes ONLY the story (title + request paragraph + story_state_schema) plus an optional
`sizing` dict that raises named knobs. The contract itself is code: each module's `params()` declares
its knob floors and its `get_errors()` enforces them — there is no per-component done-condition list.
`spec.params` is the resolved sizing (every module's floor, unioned, with the proposer's raises on
top); the build reads it.
"""

import logging
from pathlib import Path
from typing import Dict, List

from maestro.state import RunState

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "maestro" / "prompts"


def _emit(event_type: str, run_id: str, **payload) -> None:
    try:
        from api.websocket.event_bus import event_bus
        from config.time_utils import get_utc_timestamp
        event_bus.publish_sync({"type": event_type, "run_id": run_id,
                                "timestamp": get_utc_timestamp(), **payload})
    except Exception:
        logger.debug("event bus unavailable for %s", event_type)


_PNC_KEYWORDS = ("point-and-click", "point and click", "point'n'click", "pointandclick",
                 "point & click", "adventure game", "escape room", "escape-room",
                 "room escape", "hidden object", "inventory puzzle")
_CARD_KEYWORDS = ("card game", "card-game", "card battle", "play for ante", "for ante",
                  "blackjack", "poker", "wager", "gambl")
_GENRES = ("vn", "point_and_click", "card_ante")


def _classify_genre(request: str) -> str:
    """Pick the preset a request wants. Keyword match first (cheap, deterministic); only an
    ambiguous request costs a classification call. Any failure falls back to 'vn'."""
    low = request.lower()
    if any(k in low for k in _CARD_KEYWORDS):
        return "card_ante"
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


_GENRE_BLURB = {"vn": "genre_blurb_vn.txt", "point_and_click": "genre_blurb_pnc.txt",
                "card_ante": "genre_blurb_card.txt"}


def _param_floors(module_ids) -> Dict:
    """The union of every composed module's `params()` floors: int knobs take the max, list knobs
    (field-set requirements) take the union — the same merge the old baseline used."""
    import maestro.modules  # noqa: F401 — register modules + presets
    from maestro.modules import MODULE_REGISTRY

    floors: Dict = {}
    ids = ("human",) + tuple(mid for mid in module_ids if mid != "human")
    for mid in ids:
        m = MODULE_REGISTRY.get(mid)
        if m is None:
            continue
        for k, v in m.params().items():
            if isinstance(v, list):
                cur = floors.setdefault(k, [])
                for x in v:
                    if x not in cur:
                        cur.append(x)
            else:
                floors[k] = max(floors.get(k, v), v)
    return floors


def _resolve_params(module_ids, sizing: Dict) -> Dict:
    """The frozen `spec.params`: the floors, with the proposer's int raises applied (never below
    floor). Unknown names and non-int values are ignored — the floor still holds."""
    floors = _param_floors(module_ids)
    for name, value in (sizing or {}).items():
        if (isinstance(value, int) and not isinstance(value, bool)
                and isinstance(floors.get(name), int)):
            floors[name] = max(floors[name], value)
    return floors


def _spec_prompt_ctx(request: str, genre: str) -> Dict:
    import maestro.modules  # noqa: F401
    from maestro.modules import PRESETS

    blurb = (_PROMPTS_DIR / _GENRE_BLURB.get(genre, "genre_blurb_vn.txt")).read_text(
        encoding="utf-8").strip()
    modules = (PRESETS.get(genre) or PRESETS["vn"]).modules
    floors = _param_floors(modules)
    catalog = ", ".join(f"{name} (floor {v})" for name, v in floors.items()
                        if isinstance(v, int) and not isinstance(v, bool))
    return {"request": request, "genre_blurb": blurb, "sizing_knobs": catalog}


def propose_spec(request: str, run_id: str) -> Dict:
    """Draft a spec for the request and persist it (unfrozen). Returns the spec."""
    from renpy.templating import render_template
    from llm_clients.inference import PipelineAgent, JSON_SYSTEM, json_with_correction
    import maestro.modules  # noqa: F401 — ensure modules + presets registered
    from maestro.modules import PRESETS

    genre = _classify_genre(request)
    prompt = render_template(_PROMPTS_DIR / "propose_spec.txt", _spec_prompt_ctx(request, genre))
    agent = PipelineAgent(JSON_SYSTEM, max_tokens=8000)
    spec = json_with_correction(agent, prompt, "propose_spec", attempts=3)

    spec["frozen"] = False
    spec["genre"] = genre
    preset = PRESETS.get(genre)
    if preset:
        spec.setdefault("substrate", preset.substrate)
        spec.setdefault("modules", list(preset.modules))
        spec.setdefault("engine", preset.engine)
    spec.setdefault("request", request)
    modules = spec.get("modules") or list(PRESETS["vn"].modules)
    spec["params"] = _resolve_params(modules, spec.pop("sizing", None))
    spec.pop("components", None)  # contract is code now, not a per-component list

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
    # Re-resolve params in case modules / sizing changed, so floors always hold.
    if "modules" in changes or "sizing" in changes or "params" in changes:
        spec["params"] = _resolve_params(spec.get("modules") or [], spec.get("params"))
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
    spec["params"] = _resolve_params(spec.get("modules") or [], spec.get("params"))
    spec["frozen"] = True
    state.write_spec(spec)
    _emit("spec_frozen", run_id, title=spec.get("title", ""))
    return {"ok": True, "frozen": True}


def _apply_changes(spec: Dict, changes: Dict) -> None:
    """Shallow merge for top-level fields (title/request/modules/engine/params/story_state_schema)."""
    for key, value in changes.items():
        spec[key] = value
