# Kit capability catalog (spec-level)

The game is BUILT by composing the kit's ready-made building blocks. Design the game as a choice of
these blocks + plain-language rules for anything they don't cover. Do NOT describe HOW a block works
internally — name it and describe the player's experience; the implementer wires the block.

## Control schemes — pick EXACTLY ONE per steered entity (everything the player steers needs one)
- `platformer` (2D): run left/right and jump; gravity pulls down; lands on platforms. (runner, Mario-like)
- `top-down` (2D): move freely in all directions on a flat field. (twin-stick, top-down shooter, arena)
- `grid-turn` (2D): step exactly one tile per key press on a grid. (sokoban, roguelike, tile puzzle)
- `orbital-3d` (3D): THIRD-PERSON hero — the mouse orbits the camera around the character. Any "third-person" request is this. (action RPG, melee arena, 3D platformer, collectathon)
- `vehicle-3d` (3D): turn to steer and drive along the facing; momentum, can't stop instantly. Anything piloted or driven. (car, tank, boat, plane, hovercraft)
- `first-person-3d` (3D): mouse aims/looks, WASD walks and strafes; camera at the eyes. (FPS, explorer)
- `follow-3d` (3D): keyboard-only ground movement, camera auto-trails behind; NO mouse at all. (3D snake, runner, herder)

The CAMERA comes WITH the control scheme — never design a camera separately.

## Enemy / NPC behavior — name the ones you use
- `chase` (homes in on a target) · `flee` · `wander` (drifts) · `patrol` (fixed route) · `pathfind` (routes around walls)

## World & feel — name the ones you use
- `tilemap` — a grid of walls/floor from a text layout (mazes, levels, platforms)
- `scrolling` — the world is bigger than the screen; the camera follows the player (2D)
- `particles` — bursts of short-lived specks (explosions, hits, sparkles)

## Depth blocks — dialogue, quests, shops (name the ones you use)
- `dialogue` — talk to a character: named NPC, their lines, optional choices at the end
- `quest` — an accepted task with a completion condition and a reward; a quest COMPLETING does not
  end the game — progression continues (the open-world shape)
- `shop` — trade with a vendor: priced options bought with the game's currency
- `notify` — transient on-screen messages ("Got 10 gold") for small accomplishments
A game with these gets DEPTH from different NPC ROLES: design each named NPC with its own role
(vendor / quest-giver / craftsman) and its own voice, never one shared script.

## Controls — ONE activate key for everything the player does TO the world
The runtime gives the player a single **activate** key. What it does depends on what they are
FACING: the same key talks to the villager, harvests the crop in front of them, feeds the animal,
opens the chest, sleeps at the bed. So describe the controls as ONE entry ("act on what you are
facing: talk, harvest, tend, sleep"), never one key per verb — separate keys for separate verbs are
merged anyway, and the player only ever needed the one.
A key of its own is for a verb with NO target: jump, shoot, dash, brake, cycle the held tool.
Movement is never a control entry either — the control scheme owns it.

## Everything else = plain behavior rules
Spawning cadence, scoring, win/lose, timers, collectibles, doors, waves — state the RULE and the
OUTCOME in plain language ("a new enemy appears every 2 seconds", "collecting all coins wins"). The
implementer chooses the numbers and wiring.
For an open-ended game (a sandbox/settlement/life sim with no final victory) set "win": null and
carry ALL progression as quests/milestones; for a finite goal ("defeat the beast") state it as the
win — reaching it ends the game.

## What the runtime CANNOT do — design around these, never promise them
- **Sound.** The game is SILENT: no music, no soundtrack, no sound effects. A rhythm or beat-driven
  game therefore carries its beat VISUALLY — a pulsing on-screen cue the player times against — and
  is designed and described that way ("pulse", "the beat marker"), never "in time with the music".
- **Screen effects.** No shaders and no post-processing: no CRT/scanline overlay, no bloom, no
  chromatic aberration, no blur, no screen warp. Look is flat shapes, sprites and colors.
- **Typing.** No text input of any kind (no naming, no chat, no typed commands). Every choice is a
  key press or a menu option.
- **Persistence and other players.** Nothing is saved between sessions (no high-score table
  surviving a reload, no profiles) and there is no networking, no multiplayer, no leaderboard.
