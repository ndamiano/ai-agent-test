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
  color?: string; shape?: "box" | "sphere" | "ground";
  [k: string]: any;
}
type World = Entity[];

interface Rng {
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

interface Input {
  down(key: string): boolean;
  pressed(key: string): boolean;
  pointer: { x: number; y: number; down: boolean };
}

interface Camera { x: number; y: number; follow(target: Entity, worldW?: number, worldH?: number): void; }
interface Camera3 { x: number; y: number; z: number; tx: number; ty: number; tz: number; }

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
  // grid / pathfinding
  astar(start: Vec2, goal: Vec2, passable: (x: number, y: number) => boolean, opts?: AstarOpts): Vec2[];
  cellCenter(cx: number, cy: number, cell: number): Vec2;
  gridMove(e: Entity, dx: number, dy: number, cell: number, passable?: (x: number, y: number) => boolean): boolean;
  // particles
  burst(world: World, x: number, y: number, n?: number, opts?: BurstOpts): void;
  stepParticles(world: World, dt: number): void;
  // camera / audio / end
  makeCamera(): Camera;
  audio: { play(name?: string): void };
  win(msg?: string): void;
  lose(msg?: string): void;
  readonly over: null | { won: boolean; msg: string };
}

interface Config {
  width?: number; height?: number; title?: string; background?: string;
  gravity?: number; seed?: number; mode?: "2d" | "3d";
}

// The object `createGame(kit)` returns. Give `state` a concrete type (declare it in types.ts and
// use it here) to get cross-file state access checked; `any` is allowed but unchecked.
interface GameObject {
  config: Config;
  state: any;
  init?(kit: Kit): void;
  update(dt: number, input: Input, kit: Kit): void;
  draw?(g: DrawApi, kit: Kit): void;
  camera?(cam: Camera3, kit: Kit): void;
}

// Also expose the kit types under a `Kit` namespace, so a game can `extends Kit.Entity` /
// `Kit.Config` (declaration merging with the `Kit` interface above). Both `Entity` and `Kit.Entity`
// name the same type — supports the natural "extend the kit's entity with my fields" pattern.
declare namespace Kit {
  export { Entity, Config, Vec2, Vec3, World, Rect, Tilemap, Input, DrawApi, Camera, Camera3, Rng, V, GameObject };
}
