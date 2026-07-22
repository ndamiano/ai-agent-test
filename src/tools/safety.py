"""Pre-alpha safety screen — a narrow, fail-closed block on hard-illegal categories only.

Scope: sexual content involving minors (the critical, legally-mandatory category), not a
prudish filter. Mature/dark creative themes (violence-in-fiction, dark stories) are in-scope
product and are never flagged here. The term/pattern list lives in `safety_terms.json` (data,
not code) so it can be tuned without touching this module.

Two shapes of match, both fail-closed (on any hit, refuse):
  - `unambiguous_terms` — a standalone phrase that names CSAM directly (e.g. "child porn").
  - a minor descriptor (age/school-age term or a numeric age pattern) co-occurring with an
    explicit sexual term in the same text — narrows the block to the combination, not either
    half alone, so "a 10-year-old's birthday party" or "a steamy adult romance" pass on their
    own.

This is Phase 1 scope (see tasks/safety_phase1_notes.md): keyword/pattern screening only, no
classifier or hash-matching infra.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from tools.execution_context import get_user_id

logger = logging.getLogger("maestro.safety")

_TERMS_PATH = Path(__file__).parent / "safety_terms.json"


def _compile_term(term: str) -> re.Pattern:
    pattern = re.escape(term)
    if term[0].isalnum():
        pattern = r"\b" + pattern
    if term[-1].isalnum():
        pattern = pattern + r"\b"
    return re.compile(pattern, re.IGNORECASE)


def _load_terms() -> dict:
    data = json.loads(_TERMS_PATH.read_text())
    return {
        "minor_patterns": (
            [_compile_term(t) for t in data.get("minor_terms", [])]
            + [re.compile(p, re.IGNORECASE) for p in data.get("minor_age_patterns", [])]
        ),
        "sexual_patterns": [_compile_term(t) for t in data.get("sexual_terms", [])],
        "unambiguous_patterns": [_compile_term(t) for t in data.get("unambiguous_terms", [])],
    }


_TERMS = _load_terms()


@dataclass(frozen=True)
class SafetyViolation:
    category: str  # "csam_explicit" | "csam_combination"
    matched: str    # the term(s) that triggered the block — for logging, never the full text


def _first_match(patterns: List[re.Pattern], text: str) -> Optional[str]:
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def screen_text(text: Optional[str]) -> Optional[SafetyViolation]:
    """Fail-closed screen for hard-illegal (CSAM-adjacent) content. Returns a `SafetyViolation`
    if `text` should be refused, else None."""
    if not text:
        return None

    hit = _first_match(_TERMS["unambiguous_patterns"], text)
    if hit is not None:
        return SafetyViolation("csam_explicit", hit)

    minor_hit = _first_match(_TERMS["minor_patterns"], text)
    if minor_hit is None:
        return None

    sexual_hit = _first_match(_TERMS["sexual_patterns"], text)
    if sexual_hit is None:
        return None

    return SafetyViolation("csam_combination", f"{minor_hit}+{sexual_hit}")


def screen_image_prompt(prompt: Optional[str]) -> Optional[SafetyViolation]:
    """Same screen, applied at the image-generation seam (each finalized job prompt)."""
    return screen_text(prompt)


def log_violation(violation: SafetyViolation, *, user_id: Optional[str] = None,
                   source: str = "") -> None:
    """Log a blocked request, attributed to the authed user where available. Never logs the
    full input text — only the matched term(s), to avoid persisting the flagged content."""
    if user_id is None:
        try:
            user_id = get_user_id()
        except Exception:
            user_id = None
    logger.warning(
        "safety_violation category=%s source=%s user_id=%s matched=%r",
        violation.category, source, user_id or "unknown", violation.matched,
    )
