"""The spec-vs-code AUDIT: after the gates go green, the frozen spec's claims are judged one at a
time against the game's source, so a build ends on spec-exhausted rather than errors-zero — the
gates prove a game RUNS, not that its declared mechanics exist.

Claims are enumerated MECHANICALLY from the spec's fields — asked to choose its own checklist, a
small model returns one finding and stops. Each claim is judged by a bounded read→verdict subloop
(the fix loop's grounding, transplanted): the judge reads the files it needs and must cite the
traced path. Single-shot judging over pasted sources was measured wrong BOTH ways on the same code
— unanimous "broken" on a working mechanic, "delivered" swinging 11/12→5/12 between sweeps — and
tracing corrected both. Each failed claim becomes a human-note-shaped fix on the fix_from_note
lane; the driver re-gates, re-audits, and finalizes on a clean sweep or the round/step caps.

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
CLAIM_TURN_CAP = 6       # inference turns one claim may spend before it is skipped
READS_BEFORE_VERDICT = 4  # reads before the read tool is dropped and a verdict is forced

_FAIL_STATUSES = ("broken", "stub", "missing")
_STATUSES = ("delivered",) + _FAIL_STATUSES + ("blocked",)


# Movement controls are scaffold-owned law, already gated by single_mover — an
# audit verdict on them re-judges the pipeline's own wiring (the same reason authoring skips
# movement keys). Matched on the description: the spec may put movement on a
# key the scheme doesn't bind, and it is still not the game code's claim to deliver.
_MOVEMENT_CLAIM = re.compile(r"\b(move|walk|steer|drive|turn|jump)\b", re.I)


def claims_of(spec: dict) -> List[str]:
    """The claim list, enumerated mechanically from the frozen spec's own fields. The spec is the
    contract — a shallow spec yields a short list and that's as-designed; the audit never invents
    requirements the spec didn't make."""
    design = (spec or {}).get("design") or {}
    claims = [f"Control '{k}': {v}" for k, v in (design.get("controls") or {}).items()
              if isinstance(v, str) and not _MOVEMENT_CLAIM.search(v)]
    claims += [m for m in (design.get("mechanics") or []) if isinstance(m, str)]
    for field, label in (("win", "WIN"), ("lose", "LOSE"), ("render", "RENDER")):
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


def read_schemas(nreads: int) -> List[dict]:
    """The judge's toolset: read_file until it has read enough, then nothing — an empty toolset
    forces the verdict (the fix loop's force-act, repurposed: claim 12 in the validation run spent
    10 reads and never committed)."""
    return [] if nreads >= READS_BEFORE_VERDICT else [_READ_SCHEMA]


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
