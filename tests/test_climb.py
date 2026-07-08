"""Unit tests for the prompt hill-climb harness (maestro/climb.py).

The harness's reason to exist is driving the REAL AgentLoop (live LLM calls), so
`run_module`'s happy path is not unit-testable. These tests exercise everything
around that boundary — `clone_run`'s on-disk copy contract, the module-WIPE /
--keep selection logic (with the loop stubbed to a no-op), and `_cli` argument
parsing — and stop at the loop so nothing ever hits a model.
"""

import types

import pytest

from conftest import patch_run_state_for
from maestro import climb
from maestro.state import RunState


# ── clone_run: the on-disk copy contract ──────────────────────────────────────

def _seed_source(base, run_id="src", **spec_extra):
    """A source run dir with a frozen spec, two components, and the SKIP files."""
    st = RunState(base / run_id)
    st.write_spec({"title": "T", "frozen": True, "modules": ["cast"], **spec_extra})
    st.write_component("characters", [{"id": "hero"}])
    st.write_component("nodes", [{"id": "scene_01"}])
    st.write_human_todos([{"id": "todo1"}])   # human_todos.json — must NOT be cloned
    st.write_waivers([{"code": "x"}])          # waivers.json — must NOT be cloned
    st.write_story_state({"established_facts": ["stale-fact"]})  # regenerated, not copied
    return st


def test_clone_run_copies_spec_and_components(tmp_path, monkeypatch):
    # WHY: a climb clone must reproduce the upstream artifact + frozen spec so the
    # re-driven module sees the same inputs — spec and every component .json come across.
    patch_run_state_for(monkeypatch, tmp_path)
    _seed_source(tmp_path)

    new_id = climb.clone_run("src", "cast")
    dst = RunState(tmp_path / new_id).run_dir

    assert (dst / "spec.json").exists()
    assert (dst / "characters.json").exists()
    assert (dst / "nodes.json").exists()


def test_clone_run_skips_hitl_and_regenerates_story_state(tmp_path, monkeypatch):
    # WHY: the clone is a fresh comparable run — the human's per-run todos/waivers and
    # the source's accumulated story_state must NOT carry over; story_state is reseeded
    # from the spec's schema, not copied.
    patch_run_state_for(monkeypatch, tmp_path)
    _seed_source(tmp_path,
                 story_state_schema={"established_facts": ["seed-from-schema"]})

    new_id = climb.clone_run("src", "cast")
    clone = RunState(tmp_path / new_id)
    dst = clone.run_dir

    assert not (dst / "human_todos.json").exists()
    assert not (dst / "waivers.json").exists()
    # story_state exists but is the schema-seeded init, not the stale source copy.
    ss = clone.read_story_state()
    assert ss["established_facts"] == ["seed-from-schema"]
    assert ss["recent_events_tail"] == []


def test_clone_run_new_id_is_labeled_and_distinct(tmp_path, monkeypatch):
    # WHY: the clone gets a fresh run id baked with the human label so climb outputs are
    # findable and never collide with (or overwrite) the source run.
    patch_run_state_for(monkeypatch, tmp_path)
    _seed_source(tmp_path)

    new_id = climb.clone_run("src", "mylabel")

    assert new_id.startswith("mylabel-")
    assert new_id != "src"
    # two clones of the same source get different ids (uuid suffix).
    assert climb.clone_run("src", "mylabel") != new_id


def test_clone_run_missing_spec_raises(tmp_path, monkeypatch):
    # WHY: cloning a run with no frozen spec is a caller error — fail loud, don't produce
    # a half-populated run dir the loop would choke on later.
    patch_run_state_for(monkeypatch, tmp_path)
    RunState(tmp_path / "empty")  # exists on disk but has no spec.json

    with pytest.raises(ValueError, match="no spec"):
        climb.clone_run("empty", "cast")


# ── run_module: WIPE / --keep selection (loop stubbed to a no-op) ──────────────

@pytest.fixture
def stub_loop(monkeypatch):
    """Neuter the LLM/loop boundary so run_module runs its wipe logic without a model.

    run_module imports these names at call time, so patching the source module attr
    (not climb's namespace) is what the import statement resolves to.
    """
    class _FakeLoop:
        def __init__(self, *a, **k):
            pass

        def run(self):
            return types.SimpleNamespace(ok=True, steps=0, failures=[])

    monkeypatch.setattr("maestro.agent_loop.AgentLoop", _FakeLoop)
    monkeypatch.setattr("maestro.tools.build_tools", lambda *a, **k: [])
    monkeypatch.setattr("maestro.call_log.LoggingConnector", lambda *a, **k: object())
    monkeypatch.setattr("llm_clients.connector_selector.get_connector", lambda *a, **k: object())
    monkeypatch.setattr("config.settings_manager.settings_manager.get_settings",
                        lambda: {"parallel_fixes": 1})


@pytest.mark.parametrize("wipe,characters_survives", [(True, False), (False, True)])
def test_run_module_wipe_clears_only_target_component(tmp_path, monkeypatch, stub_loop,
                                                      wipe, characters_survives):
    # WHY: climb re-drives ONE module against the SAME upstream — wipe=True must clear
    # exactly the target module's component (cast -> characters.json) and leave every
    # sibling component (nodes.json) intact; --keep (wipe=False) touches nothing.
    patch_run_state_for(monkeypatch, tmp_path)
    st = RunState(tmp_path / "run")
    st.write_spec({"title": "T", "frozen": True, "modules": ["cast"]})
    st.write_component("characters", [{"id": "hero"}])
    st.write_component("nodes", [{"id": "scene_01"}])

    climb.run_module("run", "cast", wipe=wipe)

    assert (st.run_dir / "characters.json").exists() is characters_survives
    assert (st.run_dir / "nodes.json").exists()  # a sibling component is never wiped


def test_run_module_unknown_module_raises(tmp_path, monkeypatch, stub_loop):
    # WHY: asking to climb a module the run's frozen spec never selected is a caller
    # error — reject it before touching the run, don't silently drive an empty set.
    patch_run_state_for(monkeypatch, tmp_path)
    st = RunState(tmp_path / "run")
    st.write_spec({"title": "T", "frozen": True, "modules": ["cast"]})

    with pytest.raises(ValueError, match="not in spec set"):
        climb.run_module("run", "combat")


# ── _cli: argument parsing (clone_run + run_module stubbed) ────────────────────

@pytest.fixture
def capture_cli(monkeypatch, tmp_path):
    """Stub the two functions _cli drives; capture their call args for assertion."""
    patch_run_state_for(monkeypatch, tmp_path)
    calls = {}

    def _clone(src_run_id, label):
        calls["clone"] = {"src_run_id": src_run_id, "label": label}
        return "cloned-run"

    def _run(run_id, module_id, *, wipe, max_steps):
        calls["run"] = {"run_id": run_id, "module_id": module_id,
                        "wipe": wipe, "max_steps": max_steps}
        return types.SimpleNamespace(ok=True, steps=2, elapsed=0.0, failures=[])

    monkeypatch.setattr(climb, "clone_run", _clone)
    monkeypatch.setattr(climb, "run_module", _run)
    return calls


def test_cli_defaults_label_to_module_and_wipes(monkeypatch, capture_cli):
    # WHY: the documented default — no --label uses the module id as the label, and
    # without --keep the loop wipes the module's component before re-driving.
    monkeypatch.setattr("sys.argv", ["climb", "run42", "scenes"])

    rc = climb._cli()

    assert rc == 0
    assert capture_cli["clone"] == {"src_run_id": "run42", "label": "scenes"}
    assert capture_cli["run"]["module_id"] == "scenes"
    assert capture_cli["run"]["wipe"] is True
    assert capture_cli["run"]["max_steps"] == 300  # documented default


def test_cli_label_and_keep_and_max_steps(monkeypatch, capture_cli):
    # WHY: --label overrides the run tag, --keep flips to fix-mode (no wipe), and
    # --max-steps caps the drive — each flag must reach the right call argument.
    monkeypatch.setattr("sys.argv",
                        ["climb", "run42", "scenes", "--label", "tune7",
                         "--keep", "--max-steps", "12"])

    climb._cli()

    assert capture_cli["clone"]["label"] == "tune7"
    assert capture_cli["run"]["wipe"] is False
    assert capture_cli["run"]["max_steps"] == 12


def test_cli_returns_nonzero_when_run_fails(monkeypatch, capture_cli, tmp_path):
    # WHY: _cli is a build entrypoint — its exit code must reflect the loop result so a
    # failed climb (unmet done-conditions) is a nonzero exit for scripts/CI.
    monkeypatch.setattr(climb, "run_module",
                        lambda *a, **k: types.SimpleNamespace(
                            ok=False, steps=1, elapsed=0.0, failures=[]))
    monkeypatch.setattr("sys.argv", ["climb", "run42", "scenes"])

    assert climb._cli() == 1
