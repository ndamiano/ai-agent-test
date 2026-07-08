"""spec_tools state machine — propose (unfrozen draft) → amend (re-opens) → freeze (human gate).

The param/reason RESOLVERS are already covered elsewhere; this file exercises the thin,
under-tested part: the observable frozen-state transitions and the module/engine resolution
propose_spec/amend_spec drive, plus the freeze gate that build tools refuse until it flips.

propose_spec's only non-deterministic dependency is the LLM that drafts the raw spec; everything
after (module resolution, param floors, engine/presentation, persistence, freeze) is deterministic.
So we stub that one boundary (`_stub_llm`) and assert the real resolution + state it produces.
"""

import pytest

import llm_clients.inference as inference
import tools.spec_tools as spec_tools
from conftest import patch_run_state_for
from maestro.state import RunState
from maestro.tools import build_tools, SpecNotFrozen

# The foundation modules code force-includes regardless of what the proposer picks.
FOUNDATION = {"human", "assets", "state"}


@pytest.fixture
def stub_llm(monkeypatch):
    """Replace the drafting LLM with a canned raw spec (the proposer's JSON output), so
    propose_spec runs its real deterministic resolution over a controllable input."""
    def _install(raw):
        monkeypatch.setattr(inference, "PipelineAgent",
                            lambda *a, **k: object(), raising=True)
        monkeypatch.setattr(inference, "json_with_correction",
                            lambda agent, prompt, label, attempts=3: dict(raw), raising=True)
    return _install


# ── propose_spec: drafts an UNFROZEN spec with resolved modules/params ────────

def test_propose_creates_unfrozen_spec_with_foundation_and_resolved_modules(
        tmp_path, monkeypatch, stub_llm):
    # WHY: propose_spec is a DRAFT — it must persist a spec that is NOT frozen (the build refuses
    # until the human freezes), and it must resolve the proposer's picks into a buildable set with
    # the always-on foundation force-included + dependencies expanded (scenes pulls cast).
    patch_run_state_for(monkeypatch, tmp_path)
    stub_llm({"title": "My Game", "request": "a request paragraph",
              "modules": {"scenes": "the game is a story"},
              "story_state_schema": {"facts": []}})

    spec = spec_tools.propose_spec("make a visual novel", "run1")

    assert spec["frozen"] is False
    assert FOUNDATION.issubset(set(spec["modules"]))
    assert {"scenes", "cast"}.issubset(set(spec["modules"]))  # scenes + its required dep
    assert spec["substrate"] == "discrete"
    assert spec["engine"] == "renpy"
    # persisted unfrozen on disk, not just returned
    assert RunState(tmp_path / "run1").read_spec()["frozen"] is False


def test_propose_resolves_param_floors_and_pops_legacy_component_list(
        tmp_path, monkeypatch, stub_llm):
    # WHY: the contract is CODE now — propose must resolve spec.params from module floors (not a
    # per-component done-condition list) and drop any legacy `components` the proposer emitted.
    patch_run_state_for(monkeypatch, tmp_path)
    stub_llm({"title": "T", "request": "r", "modules": {"scenes": "story"},
              "components": ["should", "be", "dropped"]})

    spec = spec_tools.propose_spec("x", "run2")

    assert "components" not in spec
    # min_characters is a real int floor for the scenes/cast set; it must be present and >= 1
    assert spec["params"]["min_characters"] >= 1


def test_propose_module_reasons_split_picked_vs_auto(tmp_path, monkeypatch, stub_llm):
    # WHY: the freeze gate shows the human WHY each module is in the set — the proposer's reason for
    # what it picked, an auto note for the foundation/deps it didn't. cast is auto-pulled by scenes.
    patch_run_state_for(monkeypatch, tmp_path)
    stub_llm({"title": "T", "request": "r", "modules": {"scenes": "core narrative"}})

    reasons = spec_tools.propose_spec("x", "run3")["module_reasons"]

    assert reasons["scenes"] == "core narrative"
    assert reasons["cast"] == spec_tools._AUTO_REASON
    assert reasons["human"] == spec_tools._AUTO_REASON


def test_propose_falls_back_to_vn_bundle_when_picks_dont_compose(
        tmp_path, monkeypatch, stub_llm):
    # WHY: a build must always exist — picks with no realization module (cast alone can't be played)
    # fall back to the default visual-novel bundle rather than yielding an unbuildable spec.
    patch_run_state_for(monkeypatch, tmp_path)
    stub_llm({"title": "T", "request": "r", "modules": {"cast": "just characters"}})

    spec = spec_tools.propose_spec("x", "run4")

    # the fallback VN bundle adds story + scenes (a plain cast pick has neither)
    assert {"scenes", "story", "cast"}.issubset(set(spec["modules"]))
    assert spec["engine"] == "renpy"


@pytest.mark.parametrize("picks,presentation,exp_engine,exp_presentation", [
    # hd2d needs a walkable world AND forces godot (only godot has the 3D presenter)
    ({"world": "explorable"}, "hd2d", "godot", "hd2d"),
    # hd2d requested without a world is a dead flag — downgrade to 2d, keep the native engine
    ({"scenes": "story"}, "hd2d", "renpy", "2d"),
    # anything else is plain 2d
    ({"scenes": "story"}, None, "renpy", "2d"),
])
def test_propose_resolves_presentation(tmp_path, monkeypatch, stub_llm,
                                       picks, presentation, exp_engine, exp_presentation):
    # WHY: presentation constrains the engine. hd2d only means something with a world; asked for
    # elsewhere it must downgrade rather than ship a flag no presenter honors.
    patch_run_state_for(monkeypatch, tmp_path)
    raw = {"title": "T", "request": "r", "modules": picks}
    if presentation is not None:
        raw["presentation"] = presentation
    stub_llm(raw)

    spec = spec_tools.propose_spec("x", "runp")

    assert spec["engine"] == exp_engine
    assert spec["presentation"] == exp_presentation


# ── amend_spec: mutates an unfrozen OR frozen spec, always RE-OPENS ───────────

def test_amend_reopens_a_frozen_spec(tmp_path, monkeypatch, frozen_run):
    # WHY (the surprising rule): amend does NOT refuse on a frozen spec — every amendment un-freezes
    # it, so the build pauses until the human re-freezes (the approval action). The pause IS the
    # contract, not a rejection.
    patch_run_state_for(monkeypatch, tmp_path)
    state = frozen_run("r", modules=["human", "assets", "state", "scenes", "cast"])

    res = spec_tools.amend_spec("r", {"title": "Renamed"}, "clearer title")

    assert res["status"] == "pending_human_approval"
    persisted = state.read_spec()
    assert persisted["frozen"] is False          # re-opened
    assert persisted["title"] == "Renamed"       # change landed + persisted


@pytest.mark.parametrize("changes,field,expected", [
    ({"title": "Brand New"}, "title", "Brand New"),
    ({"story_state_schema": {"facts": ["a"]}}, "story_state_schema", {"facts": ["a"]}),
    ({"request": "a different premise"}, "request", "a different premise"),
])
def test_amend_persists_field_mutations(tmp_path, monkeypatch, frozen_run,
                                        changes, field, expected):
    # WHY: amend is the only path to changing a spec mid-build — a shallow merge of top-level fields
    # that must actually persist to disk.
    patch_run_state_for(monkeypatch, tmp_path)
    state = frozen_run("r", modules=["human", "assets", "state", "scenes", "cast"])

    spec_tools.amend_spec("r", changes, "reason")

    assert state.read_spec()[field] == expected


def test_amend_modules_rederives_engine_and_params(tmp_path, monkeypatch, frozen_run):
    # WHY: changing modules must re-run the full resolution — foundation forced, deps expanded, and
    # the engine re-derived (combat pulls world+scenes and forces godot), not left stale.
    patch_run_state_for(monkeypatch, tmp_path)
    state = frozen_run("r", modules=["human", "assets", "state", "scenes", "cast"],
                       engine="renpy")

    spec_tools.amend_spec("r", {"modules": ["combat"]}, "add combat")

    persisted = state.read_spec()
    assert "combat" in persisted["modules"]
    assert {"world", "scenes", "cast"}.issubset(set(persisted["modules"]))
    assert persisted["engine"] == "godot"
    assert persisted["frozen"] is False


@pytest.mark.parametrize("run_id,changes,reason,exc_match", [
    ("r", {"title": "x"}, "   ", "requires a reason"),   # blank reason
    ("r", {"title": "x"}, "", "requires a reason"),       # empty reason
    ("nonexistent", {"title": "x"}, "ok", "no spec"),     # amend before any spec exists
])
def test_amend_rejects_bad_calls(tmp_path, monkeypatch, frozen_run,
                                  run_id, changes, reason, exc_match):
    # WHY: amend's reason is MANDATORY (it's what the human reviews at the pause), and amending a
    # run with no spec is a caller error — both must raise, not silently no-op.
    patch_run_state_for(monkeypatch, tmp_path)
    frozen_run("r", modules=["human", "assets", "state", "scenes", "cast"])

    with pytest.raises(ValueError, match=exc_match):
        spec_tools.amend_spec(run_id, changes, reason)


# ── freeze_spec: the human gate; flips frozen and re-holds the floors ─────────

def test_freeze_flips_frozen_and_reasserts_param_floors(tmp_path, monkeypatch, frozen_run):
    # WHY: freeze is the approval — it must set frozen=True and re-resolve params so a hand-edited
    # sub-floor value can't slip past the gate (floors always hold at freeze time).
    patch_run_state_for(monkeypatch, tmp_path)
    state = frozen_run("r", modules=["human", "assets", "state", "scenes", "cast"],
                       params={"min_characters": 0}, frozen=False)

    res = spec_tools.freeze_spec("r")

    assert res == {"ok": True, "frozen": True}
    persisted = state.read_spec()
    assert persisted["frozen"] is True
    assert persisted["params"]["min_characters"] >= 1  # floor re-held, not the stored 0


def test_freeze_raises_when_no_spec(tmp_path, monkeypatch):
    # WHY: freezing a run that has no spec is a caller error, not a silent success.
    patch_run_state_for(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="no spec"):
        spec_tools.freeze_spec("nonexistent")


# ── the gate is load-bearing: build tools refuse until freeze ─────────────────

def test_freeze_satisfies_the_build_tool_frozen_gate(tmp_path, monkeypatch):
    # WHY: the whole point of freeze is that build tools refuse (SpecNotFrozen) on an unfrozen spec
    # and pass once frozen. Prove the transition end-to-end: same run, write_component blocked
    # before freeze_spec, permitted after.
    patch_run_state_for(monkeypatch, tmp_path)
    state = RunState(tmp_path / "r")
    state.write_spec({"title": "T", "frozen": False, "modules": [], "params": {}})

    before = build_tools(state.read_spec(), state, modules=[])
    with pytest.raises(SpecNotFrozen):
        before["write_component"]("story", {"x": 1})

    spec_tools.freeze_spec("r")

    after = build_tools(state.read_spec(), state, modules=[])
    res = after["write_component"]("story", {"x": 1})  # no SpecNotFrozen now
    assert res["ok"] is True
