"""card_play — wagering card matches. Owns `matches`.

The turn-resolution mechanic for gambling: an interactable's `play_match` action enters a known
card game (card_model, engine-implemented) against an NPC for a stake, and resolution flows back
out via on_win/on_lose — mirroring how combat enters an encounter and returns via on_victory.

Rule realization is Tier-1 (engine-implemented): the IR only parameterizes card_model + opponent
+ ante + payout; the engine owns the rules and the opponent. The web runtime renders it (runMatch
in engine.js); there is no Ren'Py projection yet, so a card game must build on the web engine — a
Ren'Py build fails fast (the projection fail-fast guard), which is exactly the schema-agnostic /
projection-per-engine seam made concrete.

Depends on economy (the ante spends a variable) and on premise (the opponent is a character); and
it adds `matches` to the navigation spine's build order so the play_match targets exist first.
"""

from typing import Dict, Optional

from maestro.modules import Module
from maestro import context_render as cr

_MODELS = {"high_card", "blackjack"}


def _render_context(ctx):
    return "\n".join(
        cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        + cr.scratchpad_block(ctx) + cr.upstream_block(ctx.get("upstream") or {})
        + cr.tail_block(ctx) + ["", "Call one tool to address the first to-do item."])


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
            return f"matches.matches['{mid}'] needs an 'opponent' (a premise character id)"
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
    '      "opponent": "<a premise character id>",\n'
    '      "ante": {"var": "<gold-like variable>", "amount": 50},\n'
    '      "rounds": 1,\n'
    '      "on_win":  {"effects": [{"add_var": {"var": "<gold>", "delta": 50}}], "end": {"type": "return"}},\n'
    '      "on_lose": {"effects": [{"add_var": {"var": "<gold>", "delta": -50}}], "end": {"type": "return"}}\n'
    '    }\n'
    '  }\n'
    '}\n'
    '// A MATCH is a card game an overworld hotspot starts via a play_match action.\n'
    '// card_model picks engine-implemented rules; you only set the stake + payout.\n'
    '// opponent MUST be a premise character id; ante.var MUST be a declared variable\n'
    '//   (declare it on places via set_places_meta, like any other variable).\n'
    '// on_win/on_lose: apply the payout (add_var the ante variable) then end:return to\n'
    '//   the overworld. Some place interactable must have action {type:"play_match", match:"<id>"}.'
)

MODULE = Module(
    id="card_play",
    components=("matches",),
    descriptions={"matches": "The card matches — opponents, the ante/stakes, and the win/loss "
                             "outcomes the player wagers against."},
    schemas={"matches": v_matches},
    skeletons={"matches": SKEL_MATCHES},
    baseline={"matches": [
        {"type": "count", "path": "matches.match_ids", "min": 1},
    ]},
    # premise (opponent) first; play_match targets must exist before the places that reference them.
    deps={"matches": ["premise"], "places": ["matches"]},
    tool_names=("write_match", "edit_match", "read_match"),
    mode_tools=frozenset({"write_component", "write_match", "edit_match", "read_match",
                          "read_component", "update_scratchpad", "request_review"}),
    mode_prompt="mode_matches.txt",
    render_context=_render_context,
    action_verbs=("play_match",),
    ir_slices={"card_matches": "matches"},
    projected=True,
)
