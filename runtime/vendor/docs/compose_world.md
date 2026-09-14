# compose_world(description, seed=None)

For a 3D game played outdoors call compose_world once, with a sentence saying what the landscape IS — the kinds of land and what stands on them, never a size or a count: the world chooses its own scale, answers at once with the path it will appear at, and builds while you write. Load it and read its scale from what the loader hands back:
`import { loadWorld } from './world.js';` then `const world = await loadWorld('world/world.json', { renderer, scene });`
world.group — everything the world draws; add it to your scene. Hand it your renderer and scene as shown and it sets its own sun, shadows, haze and exposure; it draws and lights its own ground, water and scenery.
world.sizeM — x and z run from 0 to that many metres, y is up, one unit is one metre.
world.heightAt(x, z) — the ground height there. Anything standing on the ground sits at that y.
world.blocking(x, z, radius) — what a circle there hits, or null. Use it so the player cannot walk through a tree, a rock or a building.
world.regions and world.regionAt(x, z) — each region is {id, category, x, z, radius} in metres. Place the things the game is about BY REGION, from those numbers.
The world is still building when the tool answers: its ground lands first, its scenery fills in on its own, and both arrive through that one path. Write the whole game against the loaded world — never wait for it, never read world.json yourself, and never write its numbers into the code as constants.
Write real, finished code. No placeholders, no TODOs, no "implement this later".
