"""The spec-vs-code AUDIT: after the gates go green, an llm turn reads the game's source against
the frozen spec and reports every promise the code does not deliver, so a build ends on
spec-exhausted rather than errors-zero — the gates prove a game RUNS, not that its declared
mechanics exist.

Claims are enumerated MECHANICALLY from the spec's fields and the model fills verdicts against the
numbered list — asked to choose its own checklist, a small model returns one finding and stops.
Each failed claim becomes a human-note-shaped fix on the fix_from_note lane; the driver re-gates,
re-audits, and finalizes on a clean sweep or the round/step caps.

FAIL-OPEN is law: an unparseable audit, an exhausted budget, a finding cap — every failure path
finalizes the build ok. A game that never finishes is worse than an incomplete one that ships.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from maestro.codegen.gates import game_files
from maestro.codegen.module import _PROMPTS, _design_block

_AUDIT_MAX_TOKENS = 8000
MAX_FINDINGS_PER_ROUND = 5

_FAIL_STATUSES = ("broken", "stub", "missing")
_STATUSES = ("delivered",) + _FAIL_STATUSES + ("blocked",)

# Gate artifacts the audit must not read: the kit types are 16KB of context the verdicts never cite,
# and the bundle/config aren't source.
_SKIP_FILES = ("engine.d.ts",)


# Movement controls are scaffold-owned law, already gated by dead_movement + single_mover — an
# audit verdict on them re-judges the pipeline's own wiring (the same reason the probe skips
# movement keys in unbound_control). Matched on the description: the spec may put movement on a
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


def build_request(spec: dict, run_dir, claims: List[str], retry: bool = False) -> Tuple[List[dict], int]:
    """The audit turn's messages. Sources ride in full — games are small and the verdicts must cite
    file:line. GENERATED files are included (the scaffold IS the other half of every control claim)
    but labeled as law."""
    from llm_clients.message_builder import MessageBuilder
    system = (_PROMPTS / "audit.txt").read_text(encoding="utf-8")
    checklist = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    sources = []
    for name, src in game_files(run_dir).items():
        if name in _SKIP_FILES:
            continue
        label = " (GENERATED — pipeline law, judge against it, never blame it)" \
            if src.lstrip().startswith("// GENERATED") else ""
        sources.append(f"# game/{name}{label}\n```ts\n{src}\n```")
    user = "\n\n".join([
        f"# CLAIM LIST — audit every claim, in order; your array has EXACTLY {len(claims)} entries",
        checklist,
        _design_block(spec),
        *sources,
    ])
    if retry:
        user += (f"\n\nCRITICAL: your previous reply was not a valid JSON array of {len(claims)} "
                 f"entries. Reply with ONLY the complete JSON array — start with `[`, one entry per "
                 f"claim, all {len(claims)}, evidence strings under 40 words with all quotes escaped.")
    return MessageBuilder(system).add_user(user).build(), _AUDIT_MAX_TOKENS


def parse_verdicts(text: str, n_claims: int) -> Tuple[Optional[List[Dict]], str]:
    """The audit reply → validated entry list, or (None, why) so the shape retries. Local-model
    audits are often degenerate — a single entry, or JSON broken by an unescaped quote inside a
    reasoning blob — and both are only catchable by strict parse + count validation."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)
    raw = blocks[-1] if blocks else (text or "")
    try:
        entries = json.loads(raw)
    except Exception as e:
        return None, f"not JSON ({e})"
    if not isinstance(entries, list):
        return None, "not a JSON array"
    if len(entries) != n_claims:
        return None, f"{len(entries)} entries for {n_claims} claims"
    for e in entries:
        if not isinstance(e, dict) or str(e.get("status", "")).lower() not in _STATUSES:
            return None, f"bad entry: {json.dumps(e)[:80]}"
    return entries, ""


def findings_from(claims: List[str], entries: List[Dict]) -> Tuple[List[Dict], int]:
    """Failed verdicts → fix-note findings, root causes only. `blocked` entries are dropped — they
    re-judge for free next round once their blocker's fix lands, so one root bug spawns one fix
    instead of a cascade. Returns (findings capped per round, n_dropped_over_cap)."""
    failed = []
    for i, e in enumerate(entries):
        if str(e.get("status", "")).lower() not in _FAIL_STATUSES:
            continue
        if e.get("blocked_by"):
            continue
        claim = claims[i] if i < len(claims) else ""
        failed.append({"claim": claim, "note": _note(claim, e)})
    return failed[:MAX_FINDINGS_PER_ROUND], max(0, len(failed) - MAX_FINDINGS_PER_ROUND)


def _note(claim: str, entry: Dict) -> str:
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
