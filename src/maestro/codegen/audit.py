"""The brief-vs-code AUDIT: once the build says it is finished, the frozen brief's claims are judged
one at a time against the game's source.

It RUNS ONCE and REPORTS. It never drives a fix — an undelivered claim is a line in the report for
the human, because a build that edits one file until a judge is satisfied does not converge on a
game.

Claims are enumerated MECHANICALLY from the brief's own fields, never chosen by the model. Each is
judged by a read→verdict subloop: the judge reads the files it needs and must cite the traced path,
which is what grounds the verdict — judging pasted sources was measured wrong in both directions on
the same code.

FAIL-OPEN is law: a claim with no verdict inside its turn cap is SKIPPED (never a finding), and every
failure path ends in a finished build. A game that never finishes is worse than an incomplete one
that ships.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from maestro.codegen.build_steps import READ_SCHEMA, Done, Infer
from llm_clients.message_builder import MessageBuilder
from maestro.codegen.staging import game_files
from maestro.services import parse_args

_PROMPTS = Path(__file__).resolve().parent / "prompts"

_MAX_TOKENS = 3000
# Runaway backstop — the judge commits via `verdict` when it has traced enough.
CLAIM_TURN_CAP = 20

_FAIL_STATUSES = ("broken", "stub", "missing")
_STATUSES = ("delivered",) + _FAIL_STATUSES + ("blocked",)

_ENDING_VOCAB = re.compile(r"\b(wins?|winning|loses?|losing|lost|game\s+over|victory|"
                           r"defeat(?:s|ed|ing)?)\b", re.I)
# "condition" is brief boilerplate ("Lose condition: …"), not something the game delivers.
_STOPWORDS = frozenset("""a an the and or but if of to in on at by for with from into onto over
under as is are was were be been being it its this that these those they them their you your
player players game games when while until once each every all any some no not do does did can
will would should must more most than then there here which who whom whose what how why also just
only both either neither about after before during through above below out off again other same
such own too very condition conditions""".split())
_RESTATEMENT_OVERLAP = 0.5


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _significant(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _restates_ending(mechanic: str, endings: List[set]) -> bool:
    """Does this mechanic re-promise what the win/lose field already promises? Judging one promise
    as two claims lets the two verdicts disagree, and then every fix for one breaks the other —
    the dedicated field is the authority. Both halves are required so a mechanic that merely
    mentions an ending while adding a rule the field doesn't cover stays a claim of its own."""
    if not _ENDING_VOCAB.search(mechanic):
        return False
    words = _significant(mechanic)
    return any(2 * len(words & end) / (len(words) + len(end)) >= _RESTATEMENT_OVERLAP
               for end in endings if words and end)


def claims_of(spec: dict) -> List[str]:
    """The claim list, enumerated mechanically from the frozen brief. A shallow brief yields a short
    list and that is as-designed — the audit never invents requirements the brief didn't make.
    `look` and `audio` are style directions, so whether code "delivered" one is a taste verdict."""
    design = (spec or {}).get("design") or {}
    endings = [_significant(design[f]) for f in ("win", "lose")
               if isinstance(design.get(f), str) and design[f]]
    claims = [m for m in (design.get("mechanics") or [])
              if isinstance(m, str) and m.strip() and not _restates_ending(m, endings)]
    for field, label in (("win", "WIN"), ("lose", "LOSE")):
        if design.get(field):
            claims.append(f"{label}: {design[field]}")
    return claims


VERDICT_SCHEMA = {"type": "function", "function": {
    "name": "verdict",
    "description": "Commit your judgement on the claim. Call this once you have traced enough — "
                   "read as many files as you need first.",
    "parameters": {"type": "object", "properties": {
        "status": {"type": "string", "enum": sorted(_STATUSES),
                   "description": "delivered | broken | stub | missing"},
        "evidence": {"type": "string",
                     "description": "file:line plus one sentence on what the traced code does"},
        "fix_note": {"type": "string",
                     "description": "empty when delivered, else one imperative sentence naming the "
                                    "smallest change that delivers the claim"},
    }, "required": ["status", "evidence"]}}}


def parse_verdict(text: str) -> Optional[Dict]:
    """The claim reply → one verdict object, or None (the subloop nudges/retries). Tolerates a
    fenced block, a bare object, or a one-entry array."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)
    raw = blocks[-1] if blocks else (text or "")
    entry = None
    try:
        entry = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                entry = json.loads(m.group(0))
            except Exception:
                return None
    if isinstance(entry, list) and len(entry) == 1:
        entry = entry[0]
    if isinstance(entry, dict) and str(entry.get("status", "")).lower() in _STATUSES:
        return entry
    return None


def is_failed(entry: Dict) -> bool:
    return str(entry.get("status", "")).lower() in _FAIL_STATUSES and not entry.get("blocked_by")


def log_verdicts(run_dir, verdicts: List[Dict]) -> None:
    """Append the round's per-claim verdicts to the run's audit_verdicts.jsonl — "which claim
    failed?" must never be unanswerable after the cursor moves on."""
    with (Path(run_dir) / "audit_verdicts.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"verdicts": verdicts}, ensure_ascii=False) + "\n")


# ── the turn ──────────────────────────────────────────────────────────────────
def step(spec, rs, cursor, tools, result):
    claims = claims_of(spec)
    ac = cursor.audit_cursor()
    if not ac.history:
        out = _claim_start(spec, rs.run_dir, claims, ac)
        cursor.set_audit(ac)
        return out

    message = (result.get("choices") or [{}])[0].get("message", {}) or {}
    content = message.get("content") or ""
    if len(content) > 2000:
        content = "[…truncated…]\n" + content[-2000:]
    calls = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
    ac.turn += 1

    # A model that answers with the JSON in content instead still lands via parse_verdict below.
    committed = next((tc for tc in calls if tc["function"]["name"] == "verdict"), None)
    if committed:
        content = json.dumps(parse_args(committed["function"].get("arguments")))

    reads = [tc for tc in calls if tc["function"]["name"] == "read_file"]
    if reads and not committed and ac.turn < CLAIM_TURN_CAP:
        ac.history.append({"role": "assistant", "content": content, "tool_calls": reads})
        for tc in reads:
            args = parse_args(tc["function"].get("arguments"))
            res = tools["read_file"](path=args.get("path") or args.get("file"))
            ac.nreads += 1
            ac.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                               "content": json.dumps(res)})
        out = _claim_infer(claims, ac)
        cursor.set_audit(ac)
        return out

    verdict = parse_verdict(content)
    if verdict is None and ac.turn < CLAIM_TURN_CAP:
        ac.history.append({"role": "assistant", "content": content})
        ac.history.append({"role": "user",
                           "content": "Call read_file to trace further, or call verdict to commit."})
        out = _claim_infer(claims, ac)
        cursor.set_audit(ac)
        return out

    claim = claims[ac.claim_idx]
    if verdict is None:
        ac.verdicts.append({"claim": claim, "status": "skipped", "evidence": ""})
    else:
        status = str(verdict.get("status", "")).lower()
        ac.verdicts.append({"claim": claim, "status": status,
                            "evidence": verdict.get("evidence", "")})
        if status == "delivered":
            ac.delivered.append(claim)
        elif is_failed(verdict):
            ac.findings.append({"claim": claim, "note": verdict.get("fix_note", ""),
                                "evidence": verdict.get("evidence", "")})
    ac.claim_idx += 1
    if ac.claim_idx < len(claims):
        out = _claim_start(spec, rs.run_dir, claims, ac)
        cursor.set_audit(ac)
        return out

    log_verdicts(rs.run_dir, ac.verdicts)
    cursor.audit_done = True
    cursor.audit_delivered = list(ac.delivered)
    cursor.phase = "build"
    cursor.set_audit(None)
    skipped = sum(1 for v in ac.verdicts if v["status"] == "skipped")
    report = f"audit: {len(ac.delivered)}/{len(claims)} delivered"
    if ac.findings:
        report += (f", {len(ac.findings)} not delivered — see audit_verdicts.jsonl: "
                   + "; ".join(f["claim"][:60] for f in ac.findings[:3]))
    if skipped:
        report += f", {skipped} skipped"
    return Done(report)


def _claim_start(spec, run_dir, claims, ac) -> Infer:
    claim = claims[ac.claim_idx]
    ac.system = (_PROMPTS / "audit_claim.txt").read_text(encoding="utf-8")
    filelist = "\n".join(f"- {name} ({len(src.splitlines())} lines)"
                         for name, src in game_files(run_dir).items())
    parts = [f"# CLAIM\n{claim}"]
    if claim in ac.anchors:
        parts.append("# ANCHOR — a previous audit verified this claim delivered against this same "
                     "code. Judge it failed ONLY if you can cite a specific regression (changed "
                     "lines that broke it); a new opinion is not a regression.")
    parts.append(f"# THE BRIEF\n{json.dumps((spec or {}).get('design') or {}, indent=1, ensure_ascii=False)}")
    parts.append(f"# FILES (read what you need)\n{filelist}")
    ac.history = [{"role": "user", "content": "\n\n".join(parts)}]
    ac.turn = 0
    ac.nreads = 0
    return _claim_infer(claims, ac)


def _claim_infer(claims, ac) -> Infer:
    msgs = MessageBuilder(ac.system).extend(ac.history).build()
    return Infer(msgs, [READ_SCHEMA, VERDICT_SCHEMA], _MAX_TOKENS,
                 report=f"audit claim {ac.claim_idx + 1}/{len(claims)} (turn {ac.turn + 1})")
