"""aspects — the LLM-facing pick layer (nouns-primary).

The proposer never names the internal engine machinery (`scenes`, `world`, `combat`, ...). It picks
concrete **aspects** — what the game DOES — and each aspect RESOLVES to its engine module(s) via
`requires` (expanded transitively by `resolve_modules`). Engine selection (`engine_for`) then reads
the resolved engine set, so an aspect that pulls `combat` still routes to Godot for free.

These are **degenerate** aspects: thin wrappers with a concrete description + `requires`, no checks
or params of their own — the reclassification that keeps the pick vocabulary uniform (the LLM always
picks aspects). A real content/modifier aspect adds its own specific `checks`/`params()` on top of
this same shape. Every aspect MUST require ≥1 engine module (enforced in `register_module`).

IDs are distinct from the engine ids they wrap (the registry is keyed by id, so `turn_combat`
cannot collide with the engine module `combat`).
"""

from maestro.modules.module import Module, register_module


class Dialogue(Module):
    id = "dialogue"
    layer = "aspect"
    requires = ("scenes",)
    description = ("Branching conversations and choices — talkable characters and dialogue trees the "
                   "player steers by what they say. The playable script of a visual novel, or the "
                   "room-talk of an explorable world.")


class Narrative(Module):
    id = "narrative"
    layer = "aspect"
    requires = ("story",)
    description = ("A dramatic through-line — a themed plot with named storylines and earned "
                   "outcomes the game builds toward. Pick when the game TELLS a story, not just "
                   "runs a mechanic.")


class Exploration(Module):
    id = "exploration"
    layer = "aspect"
    requires = ("world",)
    description = ("A walkable world of clickable rooms and screens the player moves between, with "
                   "hotspots to examine and things to pick up.")


class TurnCombat(Module):
    id = "turn_combat"
    layer = "aspect"
    requires = ("combat",)
    description = ("Turn-based fights — stats, abilities, and encounters the player battles through, "
                   "with an XP/level growth loop. Runs on the Godot engine.")


class Items(Module):
    id = "items"
    layer = "aspect"
    requires = ("inventory",)
    description = ("A carried inventory — keys, tools, and objects the player picks up and uses on "
                   "fetch/use puzzles and gated interactions.")


class RoamingEncounters(Module):
    id = "roaming_encounters"
    layer = "aspect"
    requires = ("wild_encounters",)
    description = ("Roaming danger on the map — dangerous zones that roll random, tier-scaled fights "
                   "as the player walks them, feeding the combat growth loop. Skip when every fight "
                   "is an authored set piece.")


for _m in (Dialogue(), Narrative(), Exploration(), TurnCombat(), Items(), RoamingEncounters()):
    register_module(_m)
