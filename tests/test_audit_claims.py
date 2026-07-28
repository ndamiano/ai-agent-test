"""The brief-vs-code audit: what counts as a claim, and the read→verdict subloop per claim."""
import json

import pytest

from maestro.codegen import audit, build_steps
from maestro.codegen.build_state import AuditCursor, BuildCursor
from maestro.codegen.tools import build_tools
from maestro.state import RunState

_SPEC = {"request": "a dungeon crawler", "design": {
    "title": "Crawl", "genre": "roguelike",
    "look": "muted 2D tilemap, torchlight",
    "audio": "sparse dungeon ambience",
    "scope": "one ten-minute run",
    "mechanics": ["Chests drop a random relic.",
                  "Enemies take a turn after the player moves.",
                  "Torches burn down over time."],
    "win": "Reach floor 10.",
    "lose": "Hit points reach zero."}}


class _State:
    def __init__(self, run_dir):
        self.run_dir = run_dir

    def read_spec(self):
        return _SPEC


def _reply(content="", calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [
            {"id": f"c{i}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg}]}


def _armed(tmp_path):
    (tmp_path / "game").mkdir(exist_ok=True)
    (tmp_path / "game" / "game.js").write_text("// code\n", encoding="utf-8")
    cursor = BuildCursor(build_id="b1", phase="audit")
    cursor.set_audit(AuditCursor())
    return cursor, build_tools(RunState(tmp_path)), _State(tmp_path)


# ── what is a claim ───────────────────────────────────────────────────────────
def test_claims_are_the_mechanics_plus_the_endings():
    assert audit.claims_of(_SPEC) == [
        "Chests drop a random relic.",
        "Enemies take a turn after the player moves.",
        "Torches burn down over time.",
        "WIN: Reach floor 10.",
        "LOSE: Hit points reach zero."]


def test_style_fields_are_never_claims():
    """look/audio/scope are directions, so whether code "delivered" one is a taste verdict."""
    style_only = {"design": {"look": "muted 2D tilemap", "audio": "lute", "scope": "ten minutes"}}
    assert audit.claims_of(style_only) == []


def test_a_mechanic_restating_the_lose_field_is_not_its_own_claim():
    spec = {"design": {"mechanics": ["The player loses when hit points reach zero."],
                       "lose": "The player loses when hit points reach zero."}}
    assert audit.claims_of(spec) == ["LOSE: The player loses when hit points reach zero."]


def test_a_mechanic_that_only_mentions_an_ending_stays_a_claim():
    spec = {"design": {"mechanics": ["Poison ticks each turn and can kill you, ending the run."],
                       "lose": "Hit points reach zero."}}
    assert audit.claims_of(spec) == [
        "Poison ticks each turn and can kill you, ending the run.",
        "LOSE: Hit points reach zero."]


def test_endings_are_claims_even_with_no_mechanics():
    spec = {"design": {"win": "Reach floor 10.", "lose": "Deck empty."}}
    assert audit.claims_of(spec) == ["WIN: Reach floor 10.", "LOSE: Deck empty."]


def test_claims_empty_for_a_briefless_run():
    assert audit.claims_of({}) == []
    assert audit.claims_of({"design": {}}) == []


# ── verdict parsing ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    '{"status": "delivered", "evidence": "game.js:12"}',
    '```json\n{"status": "delivered", "evidence": "game.js:12"}\n```',
    '[{"status": "delivered", "evidence": "game.js:12"}]',
    'Here is my call:\n{"status": "delivered", "evidence": "game.js:12"}',
])
def test_parse_verdict_tolerates_the_shapes_a_model_emits(text):
    assert audit.parse_verdict(text)["status"] == "delivered"


def test_parse_verdict_rejects_an_unknown_status():
    assert audit.parse_verdict('{"status": "probably fine"}') is None


# ── the per-claim subloop ─────────────────────────────────────────────────────
def test_first_turn_offers_read_and_verdict(tmp_path):
    cursor, tools, state = _armed(tmp_path)
    out = audit.step(_SPEC, state, cursor, tools, {})
    assert isinstance(out, build_steps.Infer)
    assert {t["function"]["name"] for t in out.schemas} == {"read_file", "verdict"}
    assert audit.claims_of(_SPEC)[0] in out.messages[-1]["content"]


def test_a_read_feeds_back_and_the_claim_continues(tmp_path):
    cursor, tools, state = _armed(tmp_path)
    audit.step(_SPEC, state, cursor, tools, {})
    out = audit.step(_SPEC, state, cursor, tools,
                     _reply(calls=[("read_file", {"file": "game.js"})]))
    assert isinstance(out, build_steps.Infer)
    ac = cursor.audit_cursor()
    assert ac.nreads == 1 and ac.claim_idx == 0
    assert ac.history[-1]["role"] == "tool"


def test_a_delivered_verdict_advances_to_the_next_claim(tmp_path):
    cursor, tools, state = _armed(tmp_path)
    audit.step(_SPEC, state, cursor, tools, {})
    audit.step(_SPEC, state, cursor, tools,
               _reply(calls=[("verdict", {"status": "delivered", "evidence": "game.js:12"})]))
    ac = cursor.audit_cursor()
    assert ac.claim_idx == 1
    assert ac.delivered == [audit.claims_of(_SPEC)[0]]


def test_a_failed_verdict_becomes_a_finding_and_never_a_fix(tmp_path):
    cursor, tools, state = _armed(tmp_path)
    audit.step(_SPEC, state, cursor, tools, {})
    audit.step(_SPEC, state, cursor, tools,
               _reply(calls=[("verdict", {"status": "missing", "evidence": "no relic table",
                                          "fix_note": "drop a relic on chest open"})]))
    ac = cursor.audit_cursor()
    assert len(ac.findings) == 1
    assert ac.findings[0]["evidence"] == "no relic table"
    assert cursor.phase == "audit"     # a finding does not arm anything


def test_the_turn_cap_skips_the_claim_rather_than_failing_it(tmp_path):
    cursor, tools, state = _armed(tmp_path)
    audit.step(_SPEC, state, cursor, tools, {})
    for _ in range(audit.CLAIM_TURN_CAP + 1):
        out = audit.step(_SPEC, state, cursor, tools, _reply(content="still thinking"))
        if not isinstance(out, build_steps.Infer):
            break
    ac = cursor.audit_cursor()
    assert ac.verdicts and ac.verdicts[0]["status"] == "skipped"
    assert not ac.findings


def test_an_anchored_claim_carries_the_regression_bar(tmp_path):
    cursor, tools, state = _armed(tmp_path)
    first = audit.claims_of(_SPEC)[0]
    cursor.set_audit(AuditCursor(anchors=[first]))
    out = audit.step(_SPEC, state, cursor, tools, {})
    assert "ANCHOR" in out.messages[-1]["content"]


def test_the_last_claim_logs_the_verdicts_and_returns_to_the_build(tmp_path):
    cursor, tools, state = _armed(tmp_path)
    claims = audit.claims_of(_SPEC)
    audit.step(_SPEC, state, cursor, tools, {})
    out = None
    for _ in claims:
        out = audit.step(_SPEC, state, cursor, tools,
                         _reply(calls=[("verdict", {"status": "delivered", "evidence": "game.js:1"})]))
    assert isinstance(out, build_steps.Done)
    assert f"{len(claims)}/{len(claims)} delivered" in out.report
    assert cursor.audit_done is True and cursor.phase == "build"
    assert cursor.audit_delivered == claims
    logged = (tmp_path / "audit_verdicts.jsonl").read_text().strip()
    assert len(json.loads(logged)["verdicts"]) == len(claims)
