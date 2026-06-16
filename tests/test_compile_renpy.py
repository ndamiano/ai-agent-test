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


def test_compile_reports_dangling_jump_without_repair(tmp_path, monkeypatch):
    # The agent wrote scene_01 jumping to scene_02 which it hasn't built. The agentic
    # path must NOT rewrite scene_01 — it must report the dangling jump so the agent
    # builds scene_02. And it must not invoke the LLM repair.
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_get_sdk_path", lambda: "")

    def _no_repair(*a, **k):
        raise AssertionError("_validate_and_repair must not run in the agentic path")
    monkeypatch.setattr(fns, "_validate_and_repair", _no_repair)

    (tmp_path / "brief.json").write_text(json.dumps({"title": "T"}))
    (tmp_path / "premise.json").write_text(json.dumps(
        {"characters": [{"id": "evelyn", "name": "Evelyn"}]}))
    (tmp_path / "asset_manifest.json").write_text(json.dumps(
        {"backgrounds": [{"id": "bg_office", "image_file": "o.png"}],
         "characters": [{"id": "evelyn", "image_file": "e.png"}], "cgs": [], "title_card": {}}))
    (tmp_path / "node_scripts.json").write_text(json.dumps({
        "node_ids": ["scene_01"],
        "scripts": {"scene_01": "label scene_01:\n    scene bg_office\n    jump scene_02"}}))

    result = compile_renpy(tmp_path)   # repair=False by default
    assert result["ok"] is False
    assert "scene_02" in result["reason"]
    assert "jump" in result["reason"].lower()


def test_compile_returns_structured_failure_on_malformed_content(tmp_path, monkeypatch):
    # The agent authored a premise whose character lacks an 'id' — build() raises
    # KeyError deep inside. compile_renpy must catch it and return a clean failure,
    # never propagate (that broke a live run).
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_get_sdk_path", lambda: "")

    (tmp_path / "brief.json").write_text(json.dumps({"title": "Broken"}))
    (tmp_path / "premise.json").write_text(json.dumps({"characters": [{"name": "NoId"}]}))
    (tmp_path / "asset_manifest.json").write_text(json.dumps(
        {"backgrounds": [], "characters": [], "cgs": [], "title_card": {}}))
    (tmp_path / "node_scripts.json").write_text(json.dumps({"node_ids": [], "scripts": {}}))

    result = compile_renpy(tmp_path)   # must not raise
    assert result["ok"] is False
    assert "build error" in result["reason"]
    assert "KeyError" in result["reason"]
