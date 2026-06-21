"""The discrete_state substrate: its mechanic-modules + the presets that compose them.

Importing this package registers every discrete module and preset (idempotent). A game on this
substrate advances on choice/coarse-tick (no sub-second stakes) and is validated by a static
data-walk. Both engine backends (Ren'Py, web) project the modules registered here; an engine
only adds a projection (see renpy.projections / web.projections).

Module roster:
  cast          — owns `premise` (the character cast; floor: id + name)
  assets        — owns `asset_manifest` (images)
  dialogue      — owns `nodes` as the STORY SPINE (rich premise, endings, branching, compiles)
  dialogue_npc  — owns `nodes` as SUPPORTING NPC barks (light floor; navigation is the spine)
  navigation    — owns `places` (rooms/hotspots/move); the spine when present
  economy       — vocabulary only (flags/variables/items + effects/conditions ride inside the
                  components above; no component of its own)

Presets (what the classifier emits; `genre` keeps the preset name for the UI):
  vn               = cast + assets + dialogue + economy
  point_and_click  = cast + assets + dialogue_npc + navigation + economy
"""

from maestro.modules import register_module, register_preset, Preset
from maestro.discrete import cast, assets, dialogue, navigation, economy, card_play, goal

register_module(cast.MODULE)
register_module(assets.MODULE)
register_module(dialogue.SPINE)
register_module(dialogue.NPC)
register_module(navigation.MODULE)
register_module(economy.MODULE)
register_module(card_play.MODULE)
register_module(goal.FLAG)
register_module(goal.ENDLESS)

register_preset("vn", Preset(
    substrate="discrete",
    modules=("cast", "assets", "dialogue", "economy"),
    engine="renpy"))
# Escape-room style: navigation overworld + NPC dialogue + a reachable flag/room win.
register_preset("point_and_click", Preset(
    substrate="discrete",
    modules=("cast", "assets", "dialogue_npc", "navigation", "economy", "goal_flag"),
    engine="renpy"))
# Wander-and-gamble: navigation overworld + NPC dialogue + economy stakes + card matches, and
# ENDLESS win (you play for ante indefinitely — no escape-room win hotspot). Only the web engine
# renders card_play, so the preset targets web.
register_preset("card_ante", Preset(
    substrate="discrete",
    modules=("cast", "assets", "dialogue_npc", "navigation", "economy", "card_play", "goal_endless"),
    engine="web"))
