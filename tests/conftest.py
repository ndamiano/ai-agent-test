import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.modules.context import Context

EXAMPLES_DIR = Path(__file__).parent.parent / "docs" / "examples"


# ── plain helpers (importable: `from conftest import ...`) ────────────────────
# Shared bodies so a new test copies a call, not a whole fixture. The fixtures
# below wrap these for tests that prefer injection.

def make_spec(**overrides):
    """A frozen, module-less Spec by default; override any field via kwargs."""
    doc = {"title": "T", "frozen": True, "modules": [], "params": {}}
    doc.update(overrides)
    return Spec(doc)


def seed_frozen_run(base, run_id="g", *, modules=None, params=None, owner="u1", **spec):
    """Write a frozen run dir (spec + owner) under base/run_id, return its RunState."""
    state = RunState(base / run_id)
    doc = {"title": "G", "frozen": True, "modules": modules or [], "params": params or {}}
    doc.update(spec)
    state.write_spec(doc)
    state.write_owner(owner)
    return state


def patch_run_state_for(monkeypatch, base):
    """Redirect RunState.for_run at base so router code hits real RunState on a tmp FS."""
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(base / rid)))


def load_example(name):
    """Load a docs/examples/*.json IR by stem (e.g. 'vn_crappy', 'combat_game')."""
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text())


def make_ctx(spec, artifact, *, story_state=None, waivers=None, todos=None):
    """A Context over a fake State that serves the given artifact/story-state."""
    class _S:
        run_dir = "/tmp/none"
        def load_artifact(self): return artifact
        def read_story_state(self): return story_state or {}
        def read_waivers(self): return waivers or []
        def read_human_todos(self): return todos or []
    return Context(spec=spec, state=_S(), artifact=artifact)


# ── fixtures (for tests that prefer injection over importing the helper) ──────

@pytest.fixture
def spec_factory():
    return make_spec


@pytest.fixture
def run_state(tmp_path):
    return RunState(tmp_path)


@pytest.fixture
def frozen_run(tmp_path):
    def _make(run_id="g", **kwargs):
        return seed_frozen_run(tmp_path, run_id, **kwargs)
    return _make


@pytest.fixture
def patch_for_run(monkeypatch):
    def _patch(base):
        patch_run_state_for(monkeypatch, base)
    return _patch


@pytest.fixture
def example_ir():
    return load_example


@pytest.fixture
def ctx_factory():
    return make_ctx
