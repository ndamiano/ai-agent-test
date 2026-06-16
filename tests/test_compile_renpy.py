import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.compiler import compile_renpy, compile_gate


# ── gate logic (deterministic, no SDK) ──────────────────────────────────────

def test_gate_passes_when_no_sdk():
    # error_count None = no SDK available to check → can't gate → pass
    assert compile_gate({"lint": {"error_count": None}}) is None


def test_gate_passes_clean_build():
    assert compile_gate({"lint": {"error_count": 0}, "dist_returncode": 0}) is None


def test_gate_fails_on_lint_errors():
    reason = compile_gate({"lint": {"error_count": 3}})
    assert reason and "3 lint error" in reason


def test_gate_fails_on_dist_error():
    reason = compile_gate({"lint": {"error_count": 0}, "dist_error": "renpy.sh not found"})
    assert reason == "renpy.sh not found"


def test_gate_fails_on_nonzero_returncode():
    reason = compile_gate({"lint": {"error_count": 0}, "dist_returncode": 1})
    assert reason and "returncode 1" in reason


# ── real build of a minimal artifact (SDK path stubbed out) ─────────────────

def _write_minimal_artifact(working_dir: Path):
    (working_dir / "brief.json").write_text(json.dumps({"title": "Test Game"}))
    (working_dir / "premise.json").write_text(json.dumps({
        "characters": [{"id": "alex", "name": "Alex", "color": "#c8ffc8"}],
        "protagonist_id": "alex",
    }))
    (working_dir / "asset_manifest.json").write_text(json.dumps({
        "backgrounds": [{"id": "bg_room", "image_file": "room.png"}],
        "characters": [{"id": "alex", "image_file": "alex.png"}],
        "cgs": [], "title_card": {},
    }))
    (working_dir / "node_scripts.json").write_text(json.dumps({
        "node_ids": ["scene_001"],
        "scripts": {
            "scene_001": "label scene_001:\n    scene bg_room\n    alex \"Hello.\"\n    return",
        },
    }))


def test_compile_produces_launchable_project(tmp_path, monkeypatch):
    # Stub the SDK out so the test is fast and deterministic (no lint/distribute
    # subprocess). A None lint error_count means the gate can't fail us.
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_get_sdk_path", lambda: "")

    _write_minimal_artifact(tmp_path)
    result = compile_renpy(tmp_path)

    assert result["ok"] is True
    assert result["reason"] is None
    assert result["lint_error_count"] is None

    script = tmp_path / "game_output" / "game" / "script.rpy"
    assert script.exists(), "script.rpy must be written"
    body = script.read_text(encoding="utf-8")
    assert "label start:" in body
    assert "jump scene_001" in body
    assert 'define alex = Character("Alex"' in body


def test_compile_reports_missing_artifact(tmp_path):
    result = compile_renpy(tmp_path)
    assert result["ok"] is False
    assert "missing artifact" in result["reason"]
