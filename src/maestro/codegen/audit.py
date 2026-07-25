"""The spec-vs-code AUDIT: after the gates go green, the frozen spec's claims are judged one at a
time against the game's source, so a build ends on spec-exhausted rather than errors-zero — the
gates prove a game RUNS, not that its declared mechanics exist.

Claims are enumerated MECHANICALLY from the spec's fields, never chosen by the model. Each is judged
by a read→verdict subloop: the judge reads the files it needs and must cite the traced path, which is
what grounds the verdict — judging pasted sources goes wrong in both directions. Each failed claim
becomes a human-note-shaped fix on the fix_from_note lane; the driver re-gates, re-audits, and
finalizes on a clean sweep or the round/step caps.

FAIL-OPEN is law: a claim with no verdict inside its turn cap is skipped (never a finding), an
exhausted budget finalizes ok. A game that never finishes is worse than an incomplete one that
ships.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from maestro.codegen.gates import game_files
from maestro.codegen.module import _PROMPTS, _READ_SCHEMA, _design_block

_AUDIT_MAX_TOKENS = 3000
MAX_FINDINGS_PER_ROUND = 5
# Runaway backstop — the judge commits via `verdict` when it has traced enough.
CLAIM_TURN_CAP = 20

_FAIL_STATUSES = ("broken", "stub", "missing")
_STATUSES = ("delivered",) + _FAIL_STATUSES + ("blocked",)


# Movement is scaffold-owned law — an audit verdict on it re-judges the pipeline's own wiring.
# Matched on the description: the spec may put movement on a key the scheme doesn't bind, and
# it is still not the game code's claim to deliver.
_MOVEMENT_CLAIM = re.compile(r"\b(move|walk|steer|drive|turn|jump)\b", re.I)

_ENDING_VOCAB = re.compile(r"\b(wins?|winning|loses?|losing|lost|game\s+over|victory|"
                           r"defeat(?:s|ed|ing)?)\b", re.I)
# "condition" is spec boilerplate ("Lose condition: …"), not something the game delivers.
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
    """The claim list, enumerated mechanically from the frozen spec's own fields. The spec is the
    contract — a shallow spec yields a short list and that's as-designed; the audit never invents
    requirements the spec didn't make. `render` is a look description, so whether code "delivered"
    it is a taste verdict; it stays a spec field for the data/asset stage but is never a claim."""
    design = (spec or {}).get("design") or {}
    claims = [f"Control '{k}': {v}" for k, v in (design.get("controls") or {}).items()
              if isinstance(v, str) and not _MOVEMENT_CLAIM.search(v)]
    endings = [_significant(design[f]) for f in ("win", "lose")
               if isinstance(design.get(f), str) and design[f]]
    claims += [m for m in (design.get("mechanics") or [])
               if isinstance(m, str) and not _restates_ending(m, endings)]
    for field, label in (("win", "WIN"), ("lose", "LOSE")):
        if design.get(field):
            claims.append(f"{label}: {design[field]}")
    return claims


def claim_prompt(spec: dict, run_dir, claim: str, anchored: bool) -> (str, str):
    """(system, first user message) for one claim's read→verdict subloop. No sources ride along —
    the judge reads its way in, which is what grounds the verdict."""
    system = (_PROMPTS / "audit_claim.txt").read_text(encoding="utf-8")
    filelist = "\n".join(f"- {name} ({len(src.splitlines())} lines)"
                         for name, src in game_files(run_dir).items()
                         if not name.endswith(".d.ts"))
    parts = [f"# CLAIM\n{claim}"]
    if anchored:
        parts.append("# ANCHOR — a previous audit verified this claim delivered against this same "
                     "code. Judge it failed ONLY if you can cite a specific regression (changed "
                     "lines that broke it); a new opinion is not a regression.")
    parts.append(_design_block(spec))
    parts.append(f"# FILES (read what you need)\n{filelist}")
    return system, "\n\n".join(parts)


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


def read_schemas(nreads: int) -> List[dict]:
    """Both tools stay offered: the judge reads until it decides it can commit."""
    return [_READ_SCHEMA, VERDICT_SCHEMA]


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
    """Append the round's per-claim verdicts to the run's audit_verdicts.jsonl — a sweep's judgment
    must be recoverable after the cursor moves on."""
    with (Path(run_dir) / "audit_verdicts.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"verdicts": verdicts}, ensure_ascii=False) + "\n")


def note_for(claim: str, entry: Dict) -> str:
    parts = [f"AUDIT — the game passes the functional gates, but this frozen-spec promise is not "
             f"delivered to the player:\n{claim}"]
    if entry.get("evidence"):
        parts.append(f"What the code does now: {entry['evidence']}")
    if entry.get("fix_note"):
        parts.append(f"Fix: {entry['fix_note']}")
    parts.append("Deliver the spec's promise with the smallest change that makes it real at "
                 "runtime. Never remove or stub a working mechanic to satisfy the letter of the "
                 "claim.")
    return "\n".join(parts)
