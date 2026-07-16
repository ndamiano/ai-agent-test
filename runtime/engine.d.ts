// engine.d.ts — AMBIENT types for the primitive kit. Any .ts game compiled with this file in scope
// sees `Kit`, `GameObject`, `Entity`, etc. as globals (no import needed). The kit surface and the
// game/module boundaries are typed precisely — that's where cross-file bugs hide; entity FIELDS
// stay open (`[k]: any`) so the model isn't fighting the type system over gameplay data.

interface Vec2 { x: number; y: number; }
interface Vec3 { x: number; y: number; z: number; }

interface Entity {
  x: number; y: number; z?: number;
  vx?: number; vy?: number; vz?: number;
  w?: number; h?: number; d?: number;
  dead?: boolean; grounded?: boolean;
  yaw?: number; pitch?: number; ry?: number;
  color?: string; shape?: "box" | "sphere" | "ground" | "heightfield" | "grassfield";
  [k: string]: any;
}
type World = Entity[];

interface Rng {
  (): number;                 // kit.rng() → float in [0,1); same as kit.rng.next()
  next(): number;
  range(lo: number, hi: number): number;
  int(lo: number, hi: number): number;
  pick<T>(arr: T[]): T;
  chance(p: number): boolean;
}

interface V {
  add(a: Vec2, b: Vec2): Vec2;
  sub(a: Vec2, b: Vec2): Vec2;
  scale(a: Vec2, k: number): Vec2;
  len(a: Vec2): number;
  norm(a: Vec2): Vec2;
  clamp(v: number, lo: number, hi: number): number;
}

interface Rect { x: number; y: number; w: number; h: number; }

interface Tilemap {
  at(cx: number, cy: number): string;
  solidAt(cx: number, cy: number): boolean;
  solidsNear(e: Entity): Rect[];
  w: number; h: number; tile: number;
}

interface DrawApi {
  clear(color?: string): void;
  rect(x: number, y: number, w: number, h: number, color: string): void;
  circle(x: number, y: number, r: number, color: string): void;
  line(x1: number, y1: number, x2: number, y2: number, color: string, width?: number): void;
  text(str: string, x: number, y: number, color?: string, size?: number, align?: string): void;
  sprite(img: unknown, x: number, y: number, w: number, h: number): void;
  push(cam: { x: number; y: number }): void;
  pop(): void;
}

type HudAnchor =
  | "top-left" | "top" | "top-right"
  | "left" | "center" | "right"
  | "bottom-left" | "bottom" | "bottom-right";

// A screen-space HUD element. The game RETURNS these from hud(kit); the engine draws them (same in
// 2D and 3D). The game never touches the canvas for the HUD, so it can't occlude the scene.
type HudItem =
  | { kind: "text"; text: string | number; at?: HudAnchor; color?: string; size?: number }
  | { kind: "bar"; value: number; max: number; at?: HudAnchor; color?: string; label?: string }
  | { kind: "banner"; text: string | number; color?: string }
  // a titled text card — NPC dialogue line, narration, quest log. Multi-line via "\n" in text.
  | { kind: "panel"; text: string | number; title?: string; at?: HudAnchor; color?: string }
  // a numbered choice list (quest accept/decline, shop/upgrade buy). The engine draws "1) …", "2) …";
  // the SIM owns the state: read the matching digit key (or arrows + set `selected`) in update() and
  // branch. `selected` (optional) highlights a row for arrow-key navigation.
  | { kind: "menu"; options: string[]; selected?: number; title?: string; at?: HudAnchor; color?: string }
  // a WORLD-ANCHORED waypoint label (3D): the engine projects world (x,z) to the screen each frame,
  // clamping to the screen edge with a direction hint when off-screen — quest/objective wayfinding.
  | { kind: "marker"; x: number; z: number; y?: number; text?: string; color?: string };

interface Input {
  down(key: string): boolean;
  pressed(key: string): boolean;
  pointer: { x: number; y: number; down: boolean };
  lookDX: number; lookDY: number;   // mouse-look delta this frame (first-person; feed to kit.mouseLook)
  camYaw: number;   // 3D camera's ground heading this frame; feed to kit.moveRelative for orbital control
}

interface Camera { x: number; y: number; follow(target: Entity, worldW?: number, worldH?: number): void; }
interface Camera3 { x: number; y: number; z: number; tx: number; ty: number; tz: number; }

// Anything kit.talkOpen can converse as — usually an NPC entity carrying these fields.
interface Talker { name?: string; lines: string[]; options?: string[]; [k: string]: any; }
// A quest in state.quests (managed via kit.quest.*).
interface Quest { id: string; title: string; reward: number; done: boolean; [k: string]: any; }

interface FlyerOpts { thrust?: number; turn?: number; climb?: number; drag?: number; keys?: Record<string, string>; }
interface BurstOpts { speed?: number; life?: number; color?: string; size?: number; rng?: Rng; }
interface AstarOpts { cols?: number; rows?: number; diagonal?: boolean; }

interface Kit {
  config: Config;
  rng: Rng;
  V: V;
  // entities + physics
  spawn(world: World, ent: Partial<Entity>): Entity;
  cull(world: World): void;
  integrate(e: Entity, dt: number, gravity?: number): void;
  integrate3(e: Entity, dt: number, gravity?: number): void;
  physics3(e: Entity, dt: number, gravity?: number, ground?: number): void;
  heading3(yaw: number, pitch: number): Vec3;
  flyer(e: Entity, input: Input, dt: number, opts?: FlyerOpts): Entity;
  aabb(a: Rect, b: Rect): boolean;
  resolveAabb(a: Entity, b: Rect): "left" | "right" | "top" | "bottom";
  makeTilemap(rows: string[], tile?: number, solid?: string): Tilemap;
  physics(e: Entity, dt: number, solids?: Rect[], gravity?: number): void;
  walk(e: Entity, dir: number, speed: number): void;
  jump(e: Entity, speed: number): void;
  // steering
  seek(e: Entity, target: Vec2, speed: number): number;
  flee(e: Entity, target: Vec2, speed: number): void;
  arrive(e: Entity, target: Vec2, speed: number, slow?: number): number;
  pursue(e: Entity, target: Entity, speed: number, lead?: number): number;
  wander(e: Entity, speed: number, rng?: Rng, turn?: number): void;
  // 3D steering (NPCs on the ground plane): these move x/z, APPLY dt themselves (no integrate3
  // needed) and set e.ry to face travel. Keep y on the terrain after: e.y = heightAt(e.x,e.z)+halfH.
  seek3(e: Entity, target: { x: number; z: number }, speed: number, dt: number): number;
  flee3(e: Entity, threat: { x: number; z: number }, speed: number, dt: number): void;
  wander3(e: Entity, speed: number, dt: number, rng?: Rng, turn?: number): void;
  patrol3(e: Entity, points: ({ x: number; z: number } | [number, number])[], speed: number, dt: number, arriveAt?: number): void;
  // push an entity out of centered footprint rects (e.g. WORLD.buildings) — call AFTER moving it,
  // so walkers (player included) slide around buildings instead of through them.
  avoidRects(e: Entity, rects: { x: number; z: number; w: number; d: number }[], pad?: number): void;
  // grid / pathfinding
  astar(start: Vec2, goal: Vec2, passable: (x: number, y: number) => boolean, opts?: AstarOpts): Vec2[];
  cellCenter(cx: number, cy: number, cell: number): Vec2;
  gridMove(e: Entity, dx: number, dy: number, cell: number, passable?: (x: number, y: number) => boolean): boolean;
  // particles
  burst(world: World, x: number, y: number, n?: number, opts?: BurstOpts): void;
  stepParticles(world: World, dt: number): void;
  // camera / audio / assets / end
  makeCamera(): Camera;
  // 3D third-person chase camera: sets the eye behind+above `target` AND the look-at to `target`.
  chaseCam(cam: Camera3, target: Entity, opts?: { back?: number; up?: number; lookUp?: number; faceYaw?: boolean }): void;
  // 3D control (PREFERRED): pick a whole feel with `config.controls` and call kit.drive in update — it
  // runs the matching mover, and the runtime auto-wires the matching camera from the SAME name (omit the
  // camera hook), so the mover+camera can never be mismatched. Schemes: "orbital" (WASD relative to a
  // drag-orbited follow cam — third-person default), "follow" (world-axis, camera trails travel),
  // "vehicle" (W/S drive + A/D turn), "fp" (mouse-look first person; enables pointer capture itself).
  drive(player: Entity, input: Input, dt: number, speed?: number): void;
  // selection for a {kind:"menu"} HUD item: 0-based index of the number key (1..9) pressed this
  // frame, else -1. Render the menu in hud(); branch on kit.menuPick(input) in update().
  menuPick(input: Input): number;
  // ── dialogue / shop: the WHOLE talk loop as one primitive (see Talker) ──
  // update(): const pick = kit.talkStep(state, input); if (pick) act on pick.pick;
  //           if (state.talk) return;   // paused while talking
  //           if (near && input.pressed("e")) kit.talkOpen(state, near);
  // hud():    items.push(...kit.talkHud(state))
  // A SHOP is the same loop with priced options passed to talkOpen.
  talkOpen(state: any, npc: Talker, options?: string[]): void;
  talkStep(state: any, input: Input, advanceKey?: string): null | { npc: Talker; pick: number };
  talkHud(state: any): HudItem[];
  // ── quests: milestone progression WITHOUT ending the game. Completing a quest notifies and play
  // continues — reserve kit.win/lose for the spec's DEFINITE ending. Quests live in state.quests.
  quest: {
    add(state: any, q: { id: string; title: string; reward?: number }): Quest | null;
    // marks done ONCE + announces; returns the quest — apply its .reward yourself:
    // const q = kit.quest.complete(state, "beast"); if (q) state.gold += q.reward;
    complete(state: any, id: string): Quest | null;
    active(state: any): Quest[];
    isDone(state: any, id: string): boolean;
    log(state: any, at?: HudAnchor): HudItem[];   // spread into hud(): ...kit.quest.log(this.state)
  };
  // transient on-screen toast ("Got 10 gold", "The gate opens") — drawn by the engine for a few
  // seconds, never blocks play, never ends the game. NOT for dialogue (use talk) or endings (win/lose).
  notify(msg: string, secs?: number): void;
  // movement controllers — the individual movers kit.drive dispatches to. Prefer kit.drive; reach for
  // these only for a bespoke rig. input → motion, dt-correct, no key latching. Call one per controlled
  // entity in update(); DON'T hand-roll WASD/dt. moveTopDown = 2D omni (x/y); moveTopDown3 = 3D omni on
  // the ground plane (x/z, faces travel via ry); moveTank3 = W/S drive along facing, A/D turn.
  moveTopDown(e: Entity, input: Input, dt: number, speed?: number): void;
  moveTopDown3(e: Entity, input: Input, dt: number, speed?: number): void;
  moveTank3(e: Entity, input: Input, dt: number, opts?: { speed?: number; turn?: number; back?: number }): void;
  // third-person ORBITAL: WASD relative to the camera (input.camYaw), not the world — W drives into
  // the screen, A/D strafe. Pair with a chase camera; drag orbits the view and movement follows it.
  moveRelative(e: Entity, input: Input, dt: number, speed?: number): void;
  // first-person (set config.pointerLock:true): mouseLook turns the player's yaw/pitch from the mouse;
  // fpCam puts the camera at the player's eyes looking along that aim; moveFP does WASD relative to yaw.
  mouseLook(player: Entity, input: Input, sens?: number): void;
  fpCam(cam: Camera3, player: Entity, opts?: { eye?: number }): void;
  moveFP(player: Entity, input: Input, dt: number, speed?: number): void;
  audio: { play(name?: string): void };
  // The skin: returns the preloaded sprite image for an entity's `sprite` id, or null if no
  // asset was generated (headless, or an unskinned game) — draw the placeholder shape then.
  sprite(id: string): unknown;
  win(msg?: string): void;
  lose(msg?: string): void;
  readonly over: null | { won: boolean; msg: string };
}

interface Config {
  width?: number; height?: number; title?: string; background?: string;
  gravity?: number; seed?: number; mode?: "2d" | "3d";
  pointerLock?: boolean;   // 3D: click captures the mouse for first-person look (fills input.lookDX/DY)
  // 3D control scheme — picks the mover (kit.drive) AND the camera (runtime) as one coherent pair.
  controls?: "orbital" | "follow" | "vehicle" | "fp";
  fog?: false | { near?: number; far?: number };   // 3D distance fog (default on; fades the world edge)
}

// The object `createGame(kit)` returns. Give `state` a concrete type (declare it in types.ts and
// use it here) to get cross-file state access checked; `any` is allowed but unchecked.
interface GameObject {
  config: Config;
  state: any;
  init?(kit: Kit): void;
  update(dt: number, input: Input, kit: Kit): void;
  // 2D SCENE: paint the game world onto the canvas. 3D games omit this — the scene renders from
  // world entities. NEVER draw the HUD here in a way that clears the screen; return HUD from hud().
  draw?(g: DrawApi, kit: Kit): void;
  // HUD (2D and 3D): RETURN screen-space overlay items; the engine draws them. Read-only, like draw.
  hud?(kit: Kit): HudItem[];
  camera?(cam: Camera3, kit: Kit): void;
}

// Also expose the kit types under a `Kit` namespace, so a game can `extends Kit.Entity` /
// `Kit.Config` (declaration merging with the `Kit` interface above). Both `Entity` and `Kit.Entity`
// name the same type — supports the natural "extend the kit's entity with my fields" pattern.
declare namespace Kit {
  export { Entity, Config, Vec2, Vec3, World, Rect, Tilemap, Input, DrawApi, Camera, Camera3, Rng, V, GameObject, HudItem, HudAnchor, Talker, Quest };
}
