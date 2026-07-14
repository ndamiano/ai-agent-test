# Kit capability catalog (spec-level)

The game is BUILT by composing the kit's ready-made building blocks. Design the game as a choice of
these blocks + plain-language rules for anything they don't cover. Do NOT describe HOW a block works
internally — name it and describe the player's experience; the implementer wires the block.

## Control schemes — pick EXACTLY ONE per steered entity (everything the player steers needs one)
- `platformer` (2D): run left/right and jump; gravity pulls down; lands on platforms. (runner, Mario-like)
- `top-down` (2D): move freely in all directions on a flat field. (twin-stick, top-down shooter, arena)
- `grid-turn` (2D): step exactly one tile per key press on a grid. (sokoban, roguelike, tile puzzle)
- `orbital-3d` (3D): move relative to the camera; the player drags to orbit the view. (3D platformer, marble, collectathon)
- `vehicle-3d` (3D): turn to steer and drive along the facing; momentum, can't stop instantly. (boat, car, tank, plane)
- `first-person-3d` (3D): mouse aims/looks, WASD walks and strafes; camera at the eyes. (FPS, explorer)
- `follow-3d` (3D): move on the ground in any direction, camera trails behind your heading. (3D top-down / hero)

The CAMERA comes WITH the control scheme — never design a camera separately.

## Enemy / NPC behavior — name the ones you use
- `chase` (homes in on a target) · `flee` · `wander` (drifts) · `patrol` (fixed route) · `pathfind` (routes around walls)

## World & feel — name the ones you use
- `tilemap` — a grid of walls/floor from a text layout (mazes, levels, platforms)
- `scrolling` — the world is bigger than the screen; the camera follows the player (2D)
- `particles` — bursts of short-lived specks (explosions, hits, sparkles)

## Everything else = plain behavior rules
Spawning cadence, scoring, win/lose, timers, collectibles, doors, waves — state the RULE and the
OUTCOME in plain language ("a new enemy appears every 2 seconds", "collecting all coins wins"). The
implementer chooses the numbers and wiring.
