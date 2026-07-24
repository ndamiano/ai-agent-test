"""The spec-vs-code audit: claim enumeration, verdict parsing, the audit fix shape, and the driver's
termination inversion (green gates → audit → findings → finalize), including every fail-open path."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.codegen import audit, build_state, build_steps
from maestro.codegen.build_chain import _advance_audit, _AUDIT_ROUNDS
from maestro.codegen.build_state import BuildCursor, FixCursor, error_to_dict
from maestro.modules.module import Error, ErrorType

_SPEC = {
    "frozen": True, "mode": "2d",
    "design": {
        "controls": {"W": "move up", "E": "interact"},
        "mechanics": ["Gold is earned by winning battles.", "Shops sell cards for gold."],
        "win": "Reach floor 10.",
        "lose": "Deck empty.",
        "render": "2D tilemap.",
    },
}


def _entries(statuses, overrides=None):
    out = [{"n": i + 1, "status": s, "evidence": f"game.ts:{i + 1} x", "fix_note": ""}
           for i, s in enumerate(statuses)]
    for i, ov in (overrides or {}).items():
        out[i].update(ov)
    return out


# ── claims ────────────────────────────────────────────────────────────────────
def test_claims_enumerate_controls_mechanics_and_endings():
    """Movement controls are scaffold-owned (gated by dead_movement + single_mover) — the audit
    judges only game-owned claims, so 'W: move up' is excluded and E-interact stays."""
    claims = audit.claims_of(_SPEC)
    assert claims == [
        "Control 'E': interact",
        "Gold is earned by winning battles.",
        "Shops sell cards for gold.",
        "WIN: Reach floor 10.",
        "LOSE: Deck empty.",
        "RENDER: 2D tilemap.",
    ]


def test_claims_empty_for_specless_run():
    assert audit.claims_of({}) == []
    assert audit.claims_of({"design": {}}) == []


# ── verdict parsing ───────────────────────────────────────────────────────────
def test_parse_accepts_fenced_array_and_rejects_bad_shapes():
    ok = json.dumps(_entries(["delivered", "broken"]))
    entries, _ = audit.parse_verdicts(f"noise\n```json\n{ok}\n```", 2)
    assert len(entries) == 2

    for bad, n in [("not json at all", 2),
                   (json.dumps(_entries(["delivered"])), 2),          # count mismatch
                   (json.dumps({"n": 1, "status": "broken"}), 1),     # not an array
                   (json.dumps([{"n": 1, "status": "nonsense"}]), 1)]:
        entries, why = audit.parse_verdicts(bad, n)
        assert entries is None and why


# ── findings ──────────────────────────────────────────────────────────────────
def test_findings_skip_delivered_and_blocked_and_carry_the_claim():
    claims = audit.claims_of(_SPEC)
    entries = _entries(
        ["delivered", "broken", "blocked", "missing", "delivered", "delivered", "delivered"],
        {1: {"evidence": "damage always 0", "fix_note": "Use defense."},
         2: {"blocked_by": 2}})
    findings, dropped = audit.findings_from(claims, entries)
    assert dropped == 0
    assert [f["claim"] for f in findings] == [claims[1], claims[3]]
    assert "damage always 0" in findings[0]["note"]
    assert "Use defense." in findings[0]["note"]


def test_findings_cap_per_round_and_report_the_overflow():
    claims = [f"c{i}" for i in range(8)]
    entries = _entries(["broken"] * 8)
    findings, dropped = audit.findings_from(claims, entries)
    assert len(findings) == audit.MAX_FINDINGS_PER_ROUND
    assert dropped == 8 - audit.MAX_FINDINGS_PER_ROUND


# ── the audit fix shape ───────────────────────────────────────────────────────
def _game(tmp_path):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "game.ts").write_text("export function update() {}\n", encoding="utf-8")
    (d / "main.ts").write_text("// GENERATED control scaffold\n", encoding="utf-8")
    return tmp_path


def _fc():
    err = Error(type=ErrorType.BUILD, code="audit_sweep", component="game", message="audit")
    return FixCursor(shape="audit", error=error_to_dict(err))


def _reply(entries):
    return {"choices": [{"message": {"content": "```json\n" + json.dumps(entries) + "\n```"}}]}


def test_audit_step_requests_then_harvests_findings(tmp_path):
    run_dir = _game(tmp_path)
    fc = _fc()
    out = build_steps.step("audit", _SPEC, run_dir, {}, fc, {})
    assert isinstance(out, build_steps.Infer)
    user = out.messages[-1]["content"]
    assert "1. Control 'E': interact" in user and "EXACTLY 6 entries" in user
    assert "game/game.ts" in user and "GENERATED" in user
    assert "engine.d.ts" not in user

    entries = _entries(["delivered"] * 5 + ["broken"],
                       {5: {"fix_note": "Draw the tilemap."}})
    out = build_steps.step("audit", _SPEC, run_dir, {}, fc, _reply(entries))
    assert isinstance(out, build_steps.Done)
    assert len(fc.findings) == 1 and "Draw the tilemap." in fc.findings[0]["note"]
    assert "5/6 delivered" in out.report


def test_audit_step_retries_once_then_fails_open(tmp_path):
    run_dir = _game(tmp_path)
    fc = _fc()
    build_steps.step("audit", _SPEC, run_dir, {}, fc, {})

    out = build_steps.step("audit", _SPEC, run_dir, {}, fc,
                           {"choices": [{"message": {"content": "garbage"}}]})
    assert isinstance(out, build_steps.Infer)           # one retry
    assert "CRITICAL" in out.messages[-1]["content"]

    out = build_steps.step("audit", _SPEC, run_dir, {}, fc,
                           {"choices": [{"message": {"content": "garbage"}}]})
    assert isinstance(out, build_steps.Done)            # fail-open, no findings
    assert fc.findings == []


def test_audit_step_passes_a_claimless_spec(tmp_path):
    out = build_steps.step("audit", {"design": {}}, _game(tmp_path), {}, _fc(), {})
    assert isinstance(out, build_steps.Done)


# ── the driver's termination inversion ────────────────────────────────────────
@pytest.fixture
def finalized(monkeypatch):
    from maestro.codegen import build_chain
    calls = []
    monkeypatch.setattr(build_chain, "_finalize",
                        lambda run_id, rs, cursor, ok: calls.append(ok))
    return calls


def test_green_gates_arm_an_audit_sweep_then_finalize_at_round_cap(finalized):
    cursor = BuildCursor(build_id="b", kind="build")
    for round_no in range(1, _AUDIT_ROUNDS + 1):
        assert _advance_audit("r", None, cursor) is True
        assert cursor.phase == "fix" and cursor.fix["shape"] == "audit"
        assert cursor.audit_round == round_no
        cursor.set_fix(None)
        cursor.phase = "outer"
    assert _advance_audit("r", None, cursor) is False
    assert finalized == [True]


def test_pending_findings_are_fixed_one_per_iteration(finalized):
    cursor = BuildCursor(build_id="b", kind="build", audit_round=1,
                         audit_pending=[{"claim": "c1", "note": "n1"},
                                        {"claim": "c2", "note": "n2"}])
    assert _advance_audit("r", None, cursor) is True
    assert cursor.fix["shape"] == "read_write"
    assert cursor.fix["error"]["code"] == "audit" and cursor.fix["error"]["message"] == "n1"
    assert cursor.audit_pending == [{"claim": "c2", "note": "n2"}]
    assert finalized == []


def test_a_clean_sweep_ends_the_audit_before_the_round_cap(finalized):
    cursor = BuildCursor(build_id="b", kind="build", audit_round=1, audit_done=True)
    assert _advance_audit("r", None, cursor) is False
    assert finalized == [True]


def test_step_cap_and_fix_kind_skip_the_audit(finalized):
    capped = BuildCursor(build_id="b", kind="build", step=40, max_steps=40)
    assert _advance_audit("r", None, capped) is False
    human_fix = BuildCursor(build_id="b", kind="fix")
    assert _advance_audit("r", None, human_fix) is False
    assert finalized == [True, True]


def test_audit_cursor_fields_survive_the_json_round_trip(tmp_path):
    cursor = BuildCursor(build_id="b", audit_round=1,
                         audit_pending=[{"claim": "c", "note": "n"}])
    build_state.save(tmp_path, cursor)
    loaded = build_state.load(tmp_path)
    assert loaded.audit_round == 1
    assert loaded.audit_pending == [{"claim": "c", "note": "n"}]
