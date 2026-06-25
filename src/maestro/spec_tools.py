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

# Strong signals a request wants a wandering-and-wagering card game.
_CARD_KEYWORDS = ("card game", "card-game", "card battle", "play for ante", "for ante",
                  "blackjack", "poker", "wager", "gambl")

_GENRES = ("vn", "point_and_click", "card_ante")


def _classify_genre(request: str) -> str:
    """Pick the preset a request wants. Keyword match first (cheap, deterministic); only an
    ambiguous request costs a classification call. Any failure falls back to 'vn' — the
    human freeze gate catches a wrong guess."""
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


# Friendly knob names for the baseline's path-less `min` checks (count checks name themselves off
# their path's last segment — "endings", "beats"). This is the ONLY size lever the proposer gets:
# a flat name->int it may raise, instead of re-authoring the whole done-condition JSON.
_TYPED_MIN_KNOBS = {"min_branches": "branches", "each_node_min_lines": "scene_length",
                    "each_place_min_interactables": "interactables"}


def _knob_name(check: Dict) -> str:
    path = check.get("path")
    return path.split(".")[-1] if path else _TYPED_MIN_KNOBS.get(check["type"], check["type"])


def _tunable_knobs(module_ids) -> Dict[str, Dict]:
    """name -> {component, identity, floor} for every baseline check carrying a `min`. The
    proposer raises these by name; code maps each back to the exact check to bump."""
    import maestro.discrete  # noqa: F401 — register modules
    from maestro.modules import compose, _identity

    knobs: Dict[str, Dict] = {}
    composed = compose(tuple(module_ids))
    for cid, checks in composed.baseline.items():
        for c in checks:
            if "min" in c:
                name = _knob_name(c)
                assert name not in knobs, f"sizing knob name collision: {name!r}"
                knobs[name] = {"component": cid, "identity": _identity(c), "floor": c["min"]}
    return knobs


def _apply_sizing(components: List[Dict], module_ids, sizing: Dict) -> None:
    """Raise the named baseline mins the proposer asked for (never below floor). Unknown names and
    non-int values are ignored — the floor still holds."""
    from maestro.modules import _identity

    knobs = _tunable_knobs(module_ids)
    by_id = {c["id"]: c for c in components}
    for name, value in (sizing or {}).items():
        knob = knobs.get(name)
        if knob is None or not isinstance(value, int) or isinstance(value, bool):
            continue
        comp = by_id.get(knob["component"])
        for check in comp.get("done_conditions", []) if comp else []:
            if _identity(check) == knob["identity"]:
                check["min"] = max(knob["floor"], value)


def _spec_prompt_ctx(request: str, genre: str) -> Dict:
    """Build the propose_spec prompt context. The proposer now writes ONLY the story (title +
    request paragraph + story_state_schema) plus an optional `sizing` dict that raises named
    baseline mins; the component contract itself is constructed in code from the composed modules'
    baseline (see `_spec_components`), so the prompt no longer carries the component shapes or the
    done-condition floor — that boilerplate was the model's job to transcribe, and the code already
    guarantees it."""
    import maestro.discrete  # noqa: F401 — register modules + presets
    from maestro.modules import PRESETS

    blurb = (_PROMPTS_DIR / _GENRE_BLURB.get(genre, "genre_blurb_vn.txt")).read_text(encoding="utf-8").strip()
    modules = (PRESETS.get(genre) or PRESETS["vn"]).modules
    knobs = _tunable_knobs(modules)
    catalog = ", ".join(f"{name} (floor {k['floor']})" for name, k in knobs.items())
    return {"request": request, "genre_blurb": blurb, "sizing_knobs": catalog}


def _spec_components(module_ids) -> List[Dict]:
    """Construct the spec's components straight from the composed modules — ids, build-order deps,
    and the code-enforced baseline done-conditions. The proposer LLM no longer authors the
    contract; the modules' baseline IS the contract, so build it deterministically. The human
    still reviews and edits it at the freeze gate."""
    import maestro.discrete  # noqa: F401 — register modules
    from maestro.modules import compose

    composed = compose(tuple(module_ids))
    present = set(composed.components)
    return [
        {
            "id": cid,
            "description": composed.descriptions.get(cid, ""),
            "deps": [d for d in composed.deps.get(cid, []) if d in present],
            "done_conditions": [dict(c) for c in composed.baseline.get(cid, [])],
        }
        for cid in composed.components
    ]


def propose_spec(request: str, run_id: str) -> Dict:
    """Draft a spec for the request and persist it (unfrozen). Returns the spec."""
    from renpy.templating import render_template
    from llm_clients.inference import PipelineAgent, JSON_SYSTEM, json_with_correction

    genre = _classify_genre(request)
    prompt = render_template(_PROMPTS_DIR / "propose_spec.txt", _spec_prompt_ctx(request, genre))
    agent = PipelineAgent(JSON_SYSTEM, max_tokens=8000)
    spec = json_with_correction(agent, prompt, "propose_spec", attempts=3)

    spec["frozen"] = False
    spec["genre"] = genre  # before _normalize_spec — the baseline picks its checks by module set
    # Expand the preset into the substrate + module set the build actually drives off of. The
    # proposer may override (setdefault), but a fresh spec gets the preset's defaults.
    import maestro.discrete  # noqa: F401 — ensure presets are registered
    from maestro.modules import PRESETS
    preset = PRESETS.get(genre)
    if preset:
        spec.setdefault("substrate", preset.substrate)
        spec.setdefault("modules", list(preset.modules))
        spec.setdefault("engine", preset.engine)
    spec.setdefault("request", request)
    # The proposer writes only the story (+ optional sizing) — construct the contract from the
    # modules, then apply the proposer's requested min raises on top.
    modules = spec.get("modules") or PRESETS["vn"].modules
    spec["components"] = _spec_components(modules)
    _apply_sizing(spec["components"], modules, spec.pop("sizing", None))
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
