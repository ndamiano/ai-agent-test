"""goal — the win condition, as its own composable mechanic.

Whether (and how) a game can be *won* is a mechanic, not a property of navigation. Pulling it out
of the navigation module lets a point-and-click escape room demand a reachable win while a
wander-and-gamble game stays open-ended — the same `places` graph, different win contract.

  FLAG    — the escape-room win: a goal is declared ({type: flag|room, id}) and must be
            REACHABLE — a flag goal needs a reachable hotspot that sets it plus a `win` action; a
            room goal just needs that room reachable. Adds the `goal_reachable` check to `places`.
  ENDLESS — no win state. The game loops (wander, play, repeat); there is no goal to declare and
            no win check. The IR already allows goal-less games (see game_ir_decisions.md).

A preset composes exactly one: point_and_click → FLAG, card_ante → ENDLESS.
"""

from maestro.modules import Module

FLAG = Module(
    id="goal_flag",
    baseline={"places": [
        {"type": "goal_reachable"},
    ]},
)

ENDLESS = Module(id="goal_endless")
