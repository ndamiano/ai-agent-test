"""The spec-vs-code audit: claim enumeration, the per-claim read→verdict subloop, and the driver's
termination inversion (green gates → audit → findings → finalize), including every fail-open path."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.codegen import audit, build_state, build_steps
from maestro.codegen.build_chain import _advance_audit
from maestro.codegen.build_state import BuildCursor, FixCursor, error_to_dict
from maestro.modules.module import Error, ErrorType

_SPEC = {
    "frozen": True, "mode": "2d",
    "design": {
        "controls": {"W": "move up", "E": "interact"},
        "mechanics": ["Gold is earned by winning battles.", "Shops sell cards for gold.",
                      "Cards are dealt at the start of each battle."],
        "win": "Reach floor 10.",
        "lose": "Deck empty.",
        "render": "2D tilemap.",
    },
}
_N = 6   # claims_of(_SPEC): E + 3 mechanics + win/lose (W is movement, render is not a claim)


# ── claims ────────────────────────────────────────────────────────────────────
def test_claims_enumerate_controls_mechanics_and_endings():
    """Movement controls are scaffold-owned — the audit
    judges only game-owned claims, so 'W: move up' is excluded and E-interact stays."""
    claims = audit.claims_of(_SPEC)
    assert claims == [
        "Control 'E': interact",
        "Gold is earned by winning battles.",
        "Shops sell cards for gold.",
        "Cards are dealt at the start of each battle.",
        "WIN: Reach floor 10.",
        "LOSE: Deck empty.",
    ]


def test_render_is_never_a_claim():
    """Whether code delivered a look description is a taste verdict, and the skin stage rewrites
    the visuals afterwards. The field stays in the spec for the data/asset stage."""
    assert not [c for c in audit.claims_of(_SPEC) if "2D tilemap" in c]
    render_only = {"design": {"render": "16-bit pixel art sprites with a CRT scanline overlay."}}
    assert audit.claims_of(render_only) == []


def test_a_mechanic_restating_the_lose_field_is_not_its_own_claim():
    """One promise judged twice cannot converge: every fix for one verdict breaks the other."""
    spec = {"design": {
        "mechanics": ["Slimes chase the knight.",
                      "If the player's health reaches zero, they wake up at the nearest inn with "
                      "reduced gold (lose condition)."],
        "lose": "Player health reaches zero.",
    }}
    assert audit.claims_of(spec) == [
        "Slimes chase the knight.",
        "LOSE: Player health reaches zero.",
    ]


def test_a_mechanic_restating_the_win_field_is_not_its_own_claim():
    spec = {"design": {"mechanics": ["Defeating 10 slimes wins the game immediately."],
                       "win": "Defeat 10 slimes"}}
    assert audit.claims_of(spec) == ["WIN: Defeat 10 slimes"]


def test_a_mechanic_that_only_mentions_an_ending_stays_a_claim():
    """Ending vocabulary alone never skips a mechanic — the sword-slash is a rule of its own that
    the lose field does not cover."""
    spec = {"design": {
        "mechanics": ["Pressing Space triggers a sword-slash; any slime within melee range takes "
                      "damage and is defeated if its health reaches zero.",
                      "Gold is earned by winning battles."],
        "lose": "Knight's health reaches zero",
        "win": "Defeat 10 slimes",
    }}
    assert audit.claims_of(spec) == [
        "Pressing Space triggers a sword-slash; any slime within melee range takes damage and is "
        "defeated if its health reaches zero.",
        "Gold is earned by winning battles.",
        "WIN: Defeat 10 slimes",
        "LOSE: Knight's health reaches zero",
    ]


def test_endings_are_claims_even_with_no_mechanics():
    spec = {"design": {"win": "Reach floor 10.", "lose": "Deck empty."}}
    assert audit.claims_of(spec) == ["WIN: Reach floor 10.", "LOSE: Deck empty."]


def test_claims_empty_for_specless_run():
    assert audit.claims_of({}) == []
    assert audit.claims_of({"design": {}}) == []


# ── verdict parsing ───────────────────────────────────────────────────────────
def test_parse_verdict_tolerates_object_array_and_fence():
    obj = {"status": "delivered", "evidence": "game.ts:1 x", "fix_note": ""}
    assert audit.parse_verdict(json.dumps(obj))["status"] == "delivered"
    assert audit.parse_verdict(json.dumps([obj]))["status"] == "delivered"
    assert audit.parse_verdict(f"noise\n```json\n{json.dumps(obj)}\n```")["status"] == "delivered"
    assert audit.parse_verdict("prose then " + json.dumps(obj))["status"] == "delivered"
    assert audit.parse_verdict("no json here") is None
    assert audit.parse_verdict(json.dumps({"status": "nonsense"})) is None


# ── the per-claim subloop ─────────────────────────────────────────────────────
def _game(tmp_path):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "game.ts").write_text("export function update() {}\n", encoding="utf-8")
    (d / "main.ts").write_text("// GENERATED control scaffold\n", encoding="utf-8")
    return tmp_path


def _fc(**kw):
    err = Error(type=ErrorType.BUILD, code="audit_sweep", component="game", message="audit")
    return FixCursor(shape="audit", error=error_to_dict(err), **kw)


def _tools():
    calls = []
    return {"read_file": lambda **kw: calls.append(kw) or {"ok": True, "content": "src"}}, calls


def _verdict_reply(status, evidence="game.ts:1 x", fix_note=""):
    return {"choices": [{"message": {"content": json.dumps(
        {"status": status, "evidence": evidence, "fix_note": fix_note})}}]}


def _read_reply(file="game.ts"):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "t1", "function": {"name": "read_file", "arguments": json.dumps({"file": file})}}]}}]}


def test_claim_subloop_reads_then_verdicts_then_advances(tmp_path):
    run_dir = _game(tmp_path)
    tools, reads = _tools()
    fc = _fc()
    out = build_steps.step("audit", _SPEC, run_dir, tools, fc, {})
    assert isinstance(out, build_steps.Infer)
    assert "audit claim 1/6" in out.report
    assert out.schemas and out.schemas[0]["function"]["name"] == "read_file"
    assert "# CLAIM\nControl 'E': interact" in fc.history[0]["content"]

    out = build_steps.step("audit", _SPEC, run_dir, tools, fc, _read_reply())
    assert isinstance(out, build_steps.Infer) and reads == [{"file": "game.ts"}]
    assert fc.claim_idx == 0 and fc.nreads == 1

    out = build_steps.step("audit", _SPEC, run_dir, tools, fc, _verdict_reply("delivered"))
    assert isinstance(out, build_steps.Infer)
    assert "audit claim 2/6" in out.report
    assert fc.claim_idx == 1 and fc.delivered == [audit.claims_of(_SPEC)[0]]
    assert fc.nreads == 0 and fc.turn == 0   # per-claim state reset


def test_failed_verdict_becomes_a_finding_with_the_evidence(tmp_path):
    run_dir = _game(tmp_path)
    tools, _ = _tools()
    fc = _fc()
    build_steps.step("audit", _SPEC, run_dir, tools, fc, {})
    build_steps.step("audit", _SPEC, run_dir, tools, fc,
                     _verdict_reply("broken", "gold never spent", "Add a shop."))
    assert len(fc.findings) == 1
    note = fc.findings[0]["note"]
    assert "gold never spent" in note and "Add a shop." in note
    assert fc.delivered == []


def test_reads_are_never_capped_and_the_turn_cap_skips_the_claim(tmp_path):
    """The judge decides when it has traced enough. A read quota made it guess: one claim judged on
    2 reads called a promised segment-respawn delivered from two unused constants."""
    run_dir = _game(tmp_path)
    tools, _ = _tools()
    fc = _fc()
    build_steps.step("audit", _SPEC, run_dir, tools, fc, {})
    for _ in range(8):                       # well past the old 4-read quota
        out = build_steps.step("audit", _SPEC, run_dir, tools, fc, _read_reply())
    names = {s["function"]["name"] for s in out.schemas}
    assert names == {"read_file", "verdict"}     # both still offered, however much it has read

    # Garbage until the turn cap: the claim is SKIPPED (fail-open), never a finding.
    while fc.claim_idx == 0:
        out = build_steps.step("audit", _SPEC, run_dir, tools, fc,
                               {"choices": [{"message": {"content": "no json"}}]})
    assert fc.verdicts[0]["status"] == "skipped"
    assert fc.findings == [] and fc.delivered == []
    assert "audit claim 2/6" in out.report


def test_verdict_tool_call_commits_the_judgement(tmp_path):
    """Committing is a tool call now, not a bare JSON reply the parser has to recognise."""
    run_dir = _game(tmp_path)
    tools, _ = _tools()
    fc = _fc()
    build_steps.step("audit", _SPEC, run_dir, tools, fc, {})
    reply = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "verdict", "arguments": json.dumps(
            {"status": "broken", "evidence": "health.ts:9 calls kit.lose()",
             "fix_note": "Respawn at the segment start instead."})}}]}}]}
    build_steps.step("audit", _SPEC, run_dir, tools, fc, reply)
    assert fc.verdicts[0]["status"] == "broken"
    assert "kit.lose()" in fc.verdicts[0]["evidence"]
    assert len(fc.findings) == 1


def test_round_finishes_with_report_log_and_finding_cap(tmp_path):
    run_dir = _game(tmp_path)
    tools, _ = _tools()
    fc = _fc()
    out = build_steps.step("audit", _SPEC, run_dir, tools, fc, {})
    statuses = ["delivered", "broken", "broken", "broken", "broken", "broken"]
    for s in statuses:
        out = build_steps.step("audit", _SPEC, run_dir, tools, fc, _verdict_reply(s))
    assert isinstance(out, build_steps.Done)
    assert "1/6 delivered" in out.report and "5 finding(s)" in out.report
    assert len(fc.findings) == audit.MAX_FINDINGS_PER_ROUND
    logged = [json.loads(l) for l in
              (tmp_path / "audit_verdicts.jsonl").read_text().splitlines()]
    assert len(logged[0]["verdicts"]) == _N
    assert logged[0]["verdicts"][0]["status"] == "delivered"


def test_anchored_claim_carries_the_anchor_block(tmp_path):
    run_dir = _game(tmp_path)
    tools, _ = _tools()
    claims = audit.claims_of(_SPEC)
    fc = _fc(anchors=[claims[0]])
    build_steps.step("audit", _SPEC, run_dir, tools, fc, {})
    assert "# ANCHOR" in fc.history[0]["content"]
    build_steps.step("audit", _SPEC, run_dir, tools, fc, _verdict_reply("delivered"))
    assert "# ANCHOR" not in fc.history[0]["content"]   # claim 2 is not anchored


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


def test_green_gates_arm_ONE_audit_sweep_then_finalize(finalized):
    """The audit reports; it does not iterate. Rounds of judge-then-fix edited one file until a
    judge was satisfied — 208 of 227 steps on the measured run, with round 2 scoring worse than
    round 1 — and the artifact was sediment."""
    cursor = BuildCursor(build_id="b", kind="build")
    assert _advance_audit("r", None, cursor) is True
    assert cursor.phase == "fix" and cursor.fix["shape"] == "audit"
    assert cursor.audit_round == 1
    cursor.set_fix(None)
    cursor.phase = "outer"

    assert _advance_audit("r", None, cursor) is False      # no second round, ever
    assert finalized == [True]


def test_a_clean_sweep_ends_the_audit(finalized):
    cursor = BuildCursor(build_id="b", kind="build", audit_round=1, audit_done=True)
    assert _advance_audit("r", None, cursor) is False
    assert finalized == [True]


def test_findings_never_arm_a_fix(finalized):
    """A finding is a line in the report for the human, not work for the loop — there is no pending
    queue for one to land in."""
    cursor = BuildCursor(build_id="b", kind="build", audit_round=1, audit_done=True)
    assert not hasattr(cursor, "audit_pending")
    assert _advance_audit("r", None, cursor) is False
    assert finalized == [True]


def test_step_cap_and_fix_kind_skip_the_audit(finalized):
    capped = BuildCursor(build_id="b", kind="build", step=40, max_steps=40)
    assert _advance_audit("r", None, capped) is False
    human_fix = BuildCursor(build_id="b", kind="fix")
    assert _advance_audit("r", None, human_fix) is False
    assert finalized == [True, True]


def test_first_sweep_of_a_new_audit_build_carries_prior_anchors(finalized):
    cursor = BuildCursor(build_id="b2", kind="audit",
                         audit_delivered=["Gold is earned by winning battles."])
    assert _advance_audit("r", None, cursor) is True
    assert cursor.fix["shape"] == "audit"
    assert cursor.fix["anchors"] == ["Gold is earned by winning battles."]


def test_audit_kickoff_gets_the_200_step_default(monkeypatch):
    """audit_run must not pin its own cap — kickoff owns the per-kind defaults (an explicit 40 here
    finalized a live run fail-open mid-round at step 43)."""
    from maestro.codegen import build_chain, run as run_mod
    seen = {}
    monkeypatch.setattr(build_chain.db_store, "create_build", lambda rid, kind: "b")
    monkeypatch.setattr(build_chain.db_store, "build_started", lambda b: None)
    monkeypatch.setattr(build_chain, "start_build",
                        lambda rid, bid, **kw: seen.update(kw))
    monkeypatch.setattr(run_mod, "_await_build", lambda rid: None)
    run_mod.audit_run("r1")
    assert seen["kind"] == "audit" and seen["max_steps"] == 200


def test_audit_cursor_fields_survive_the_json_round_trip(tmp_path):
    cursor = BuildCursor(build_id="b", audit_round=1, audit_done=True,
                         audit_delivered=["Gold is earned by winning battles."])
    build_state.save(tmp_path, cursor)
    loaded = build_state.load(tmp_path)
    assert loaded.audit_round == 1 and loaded.audit_done is True
    assert loaded.audit_delivered == ["Gold is earned by winning battles."]
