"""card_play

This module defines everything needed to create a card game. Currently, we do not have a
custom built system that supports any type of card game. This is a future todo.

Example games:
  - "a riverboat gambler bets his way to freedom"  — cast + world + card_play
  - "a back-alley blackjack hustle for rent money"  — cast + world + card_play
"""

from typing import Dict, List, Optional, Tuple

from maestro.modules import checks
from maestro.modules.module import Error, ErrorType, Module, register_module

_MODELS = {"high_card", "blackjack"}


def v_matches(c: Dict) -> Optional[str]:
    match_ids = c.get("match_ids")
    matches = c.get("matches")
    if not isinstance(match_ids, list) or not match_ids:
        return "matches.match_ids must be a non-empty list of match id strings"
    if not isinstance(matches, dict):
        return "matches.matches must be an object mapping match_id -> {card_model, opponent, ante}"
    for mid in match_ids:
        m = matches.get(mid)
        if not isinstance(m, dict):
            return f"matches.matches is missing an object for match id '{mid}'"
        if m.get("card_model") not in _MODELS:
            return f"matches.matches['{mid}'].card_model must be one of {sorted(_MODELS)}"
        if not m.get("opponent"):
            return f"matches.matches['{mid}'] needs an 'opponent' (a characters component id)"
        ante = m.get("ante")
        if not isinstance(ante, dict) or not ante.get("var") or "amount" not in ante:
            return f"matches.matches['{mid}'].ante must be {{var, amount}} (the staked variable)"
    return None


SKEL_MATCHES = (
    '{\n'
    '  "match_ids": ["match_<opponent>"],\n'
    '  "matches": {\n'
    '    "match_<opponent>": {\n'
    '      "card_model": "blackjack",            // one of: high_card, blackjack\n'
    '      "deck_model": "standard_52",\n'
    '      "opponent": "<a characters component id>",\n'
    '      "ante": {"var": "<gold-like variable>", "amount": 50},\n'
    '      "rounds": 1,\n'
    '      "on_win":  {"effects": [{"add_var": {"var": "<gold>", "delta": 50}}], "end": {"type": "return"}},\n'
    '      "on_lose": {"effects": [{"add_var": {"var": "<gold>", "delta": -50}}], "end": {"type": "return"}}\n'
    '    }\n'
    '  }\n'
    '}\n'
    '// A MATCH is a card game an overworld hotspot starts via a play_match action.\n'
    '// card_model picks engine-implemented rules; you only set the stake + payout.\n'
    '// opponent MUST be a characters component id; ante.var MUST be a declared variable\n'
    '//   (declare it on places via set_places_meta, like any other variable).\n'
    '// on_win/on_lose: apply the payout (add_var the ante variable) then end:return to\n'
    '//   the overworld. Some place interactable must have action {type:"play_match", match:"<id>"}.'
)


class CardPlay(Module):
    id = "card_play"
    description = ("Wagering card matches against an NPC for a stake (blackjack/poker-style). "
                   "Web engine only.")
    requires = ("world",)
    priority = 40
    component = "matches"
    mode_prompt = "matches_write.txt"
    mode_tools = frozenset({"write_component", "write_match", "edit_match", "read_match",
                            "read_component", "update_scratchpad", "request_review"})
    skeleton = SKEL_MATCHES
    schemas = {"matches": v_matches}
    skeletons = {"matches": SKEL_MATCHES}
    projected = True
    tool_names = ("write_match", "edit_match", "read_match")

    def params(self) -> Dict:
        return {"min_matches": 1}

    def affected_components(self) -> Tuple[str, ...]:
        return ("matches",)

    def get_errors(self, context) -> List[Error]:
        e = checks.as_error(
            checks.count(context.artifact, "matches.match_ids", min=context.param("min_matches", 1)),
            type=ErrorType.BUILD, code="min_matches", component="matches")
        return [e] if e else []


MODULE = CardPlay()
register_module(MODULE)
