/* Loading a generated world into three.js.
 *
 * The world's files are the contract: a float32 height field, region weight
 * maps, one material per region, and GLB meshes with placements in metres.
 * Nothing here knows what game is being played on top of it — it gives back a
 * group to add to a scene, a height sampler, and a list of colliders. It is the
 * same file the pipeline's own renderer draws its views with, so what a build
 * looks at is what a player walks in.
 *
 * The terrain is drawn by blending each region's material at world-space UVs
 * scaled to its own repeat distance. One baked texture cannot do this: a
 * material repeating every 3 m across 600 m repeats 200 times, and no bake
 * resolution carries that. The stochastic tiling is what keeps the repeat from
 * reading as a grid: each triangular cell samples the texture at its own random
 * offset and a fragment mixes the three cells at its corners.
 */
import * as THREE from './three.module.js';
import { GLTFLoader } from './GLTFLoader.js';

const SUN_COLOUR = 0xfff2e0;
const SKY_COLOUR = 0x9fc4e8;
// Ambient is not the sky's own colour: a world lit by sky alone comes out blue
// everywhere the sun does not reach, and every slope facing away from it reads
// as night. This is the sky pulled most of the way to neutral, and bright
// enough that shadowed ground still shows what it is made of.
const AMBIENT_COLOUR = 0xd8dcd8;
// Balanced against the sun rather than set on its own: ambient and sky together
// are what a shadow is lit BY, so raising them past the sun erases the shadow
// the sun casts. These are a shaded slope at roughly a third of a lit one.
const AMBIENT_ENERGY = 0.9;
const HEMI_ENERGY = 0.45;
const SUN_ENERGY = 3.1;
const TONEMAP_EXPOSURE = 1.25;
const COLLIDES_ABOVE_M = 0.6;

// Slope past which the ground stops being whatever the region is made of and
// starts being what it is made OF: rock. A law, not a look — a 40-degree face
// holds no soil in any world.
const ROCK_SLOPE_START = Math.cos(THREE.MathUtils.degToRad(30.0));
const ROCK_SLOPE_FULL = Math.cos(THREE.MathUtils.degToRad(45.0));
// Snow lies on the top quarter of a world's own relief, and only in the regions
// that were planned as high ground. Absolute metres would put snow on a 30 m
// hill in one world and none on a 300 m peak in another.
const SNOW_RELIEF_START = 0.74;
const SNOW_RELIEF_FULL = 0.92;
const HIGH_CATEGORIES = new Set(['mountain', 'cliff', 'alpine', 'highland']);
// The second albedo tap, in multiples of the region's own repeat distance. One
// tile at 3 m repeated over 800 m is 266 identical squares; the same tile at
// 24 m under a low-frequency mix is the same material at two readings of it.
const MACRO_SCALE = 8.0;

/* The splat, as a patch on MeshStandardMaterial rather than a shader of its own.
 *
 * A raw shader is outside everything three does for a material: no shadow map,
 * no fog, no tone mapping, no environment. Everything here is injected into the
 * standard chain, so the ground is lit by the same lights, in the same units,
 * as the GLBs standing on it.
 */
function terrainChunks(count) {
  // Unrolled per region: GLSL ES 3.00 will not index a sampler array with a
  // loop variable, and a world has as many samplers as it has regions.
  const taps = (name) => Array.from({ length: count }, (_, i) =>
    `  if (i == ${i}) { return textureGrad(u${name}[${i}], uv, dx, dy).rgb; }`).join('\n');
  return {
    common: `
uniform sampler2D uWeightsA;
uniform sampler2D uWeightsB;
uniform sampler2D uAlbedo[${count}];
uniform sampler2D uNormal[${count}];
uniform float uRepeats[${count}];
uniform float uHigh[${count}];
uniform float uSnowLow;
uniform float uSnowHigh;
uniform float uDetailNear;
uniform float uDetailFar;
varying vec2 vTerrainUv;
varying vec3 vTerrainTangent;
varying float vGroundY;
varying float vAltitude;
vec3 gTerrainNormal;
float gTerrainDetail;

vec2 hash22(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  q += dot(q, q.yzx + 33.33);
  return fract((q.xx + q.yz) * q.zy);
}

float hash21(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * 0.1031);
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}

float valueNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash21(i), hash21(i + vec2(1.0, 0.0)), f.x),
    mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), f.x),
    f.y);
}

void triangleGrid(vec2 uv, out vec3 w, out vec2 v1, out vec2 v2, out vec2 v3) {
  vec2 skewed = mat2(vec2(1.0, 0.0), vec2(-0.57735, 1.15470)) * (uv * 3.464);
  vec2 base = floor(skewed);
  vec3 bary = vec3(fract(skewed), 0.0);
  bary.z = 1.0 - bary.x - bary.y;
  float upper = step(0.0, -bary.z);
  float flip = 2.0 * upper - 1.0;
  w = vec3(-bary.z * flip, upper - bary.y * flip, upper - bary.x * flip);
  vec3 sharp = w * w * w;
  w = sharp / max(sharp.x + sharp.y + sharp.z, 0.0001);
  v1 = base + vec2(upper, upper);
  v2 = base + vec2(upper, 1.0 - upper);
  v3 = base + vec2(1.0 - upper, upper);
}

float weightFor(int i, vec2 uv) {
  vec4 a = texture2D(uWeightsA, uv);
  vec4 b = texture2D(uWeightsB, uv);
  if (i == 0) return a.r;
  if (i == 1) return a.g;
  if (i == 2) return a.b;
  if (i == 3) return a.a;
  if (i == 4) return b.r;
  if (i == 5) return b.g;
  if (i == 6) return b.b;
  return b.a;
}

vec3 albedoFor(int i, vec2 uv, vec2 dx, vec2 dy) {
${taps('Albedo')}
  return vec3(0.5);
}

vec3 normalFor(int i, vec2 uv, vec2 dx, vec2 dy) {
${taps('Normal')}
  return vec3(0.5, 0.5, 1.0);
}

// The tiling offsets jump from one triangular cell to the next, so the implicit
// derivative of the offset UV is garbage exactly on the cell edges — which the
// hardware reads as "this fragment covers the whole texture" and answers with
// the smallest mip. That is what drew a dashed white lattice over every close
// view. The gradients of the UNBROKEN uv are the true footprint, so they are
// carried through and every tap is a textureGrad.
void splat(int i, vec2 uv, vec2 dx, vec2 dy, out vec3 albedo, out vec3 surface) {
  vec3 w; vec2 c1; vec2 c2; vec2 c3;
  triangleGrid(uv, w, c1, c2, c3);
  vec2 u1 = uv + hash22(c1);
  vec2 u2 = uv + hash22(c2);
  vec2 u3 = uv + hash22(c3);
  albedo = albedoFor(i, u1, dx, dy) * w.x + albedoFor(i, u2, dx, dy) * w.y
    + albedoFor(i, u3, dx, dy) * w.z;
  surface = normalFor(i, u1, dx, dy) * w.x + normalFor(i, u2, dx, dy) * w.y
    + normalFor(i, u3, dx, dy) * w.z;
}
`,
    vertex: `
  vTerrainUv = transformed.xz / uWorldSize;
  vAltitude = transformed.y;
  vec3 groundNormal = normalize(objectNormal);
  vGroundY = groundNormal.y;
  // The terrain's UVs are the world plane itself, so the tangent frame is the
  // world axes and the map can be applied without one being carried per vertex.
  vTerrainTangent = normalize(
    normalMatrix * normalize(cross(vec3(0.0, 1.0, 0.0), groundNormal) + vec3(0.001, 0.0, 0.0)));
`,
    map: `
  vec2 uvw = vTerrainUv;
  vec2 dxw = dFdx(uvw);
  vec2 dyw = dFdy(uvw);
  // Detail is dropped with distance rather than trusted to the mip chain: a
  // stochastic tap far away is three different mips fighting, and the far field
  // shimmers frame to frame under any camera that moves.
  gTerrainDetail = 1.0 - smoothstep(uDetailNear, uDetailFar, length(vViewPosition));
  // Never all of one scale: the wide tap alone is a field of soft blobs and the
  // fine tap alone is the smear it was brought in to break.
  float macroMix = clamp(valueNoise(uvw * 3.0), 0.18, 0.68);

  vec3 colour = vec3(0.0);
  vec3 surface = vec3(0.0);
  float total = 0.0;
  float high = 0.0;
  for (int i = 0; i < ${count}; i++) {
    float w = weightFor(i, uvw);
    if (w <= 0.002) continue;
    float repeat = uRepeats[i];
    vec3 fineA; vec3 fineN;
    splat(i, uvw * repeat, dxw * repeat, dyw * repeat, fineA, fineN);
    float macro = repeat / ${MACRO_SCALE.toFixed(1)};
    vec3 wideA; vec3 wideN;
    splat(i, uvw * macro, dxw * macro, dyw * macro, wideA, wideN);
    colour += mix(fineA, wideA, macroMix) * w;
    surface += mix(fineN, wideN, macroMix) * w;
    total += w;
    high += uHigh[i] * w;
  }
  total = max(total, 0.0001);
  colour /= total;
  surface /= total;
  high /= total;

  // A gentle drift in hue and value over hundreds of metres. Ground is never one
  // colour across a field, and without this the macro mix still reads as one
  // material lit evenly.
  float tintA = valueNoise(uvw * 6.0 + 11.3);
  float tintB = valueNoise(uvw * 3.5 + 3.7);
  colour *= vec3(
    1.0 + (tintA - 0.5) * 0.14,
    1.0 + (tintB - 0.5) * 0.10,
    1.0 + ((tintA + tintB) * 0.5 - 0.5) * 0.18);

  float luma = dot(colour, vec3(0.2126, 0.7152, 0.0722));
  float rock = 1.0 - smoothstep(${ROCK_SLOPE_FULL.toFixed(5)}, ${ROCK_SLOPE_START.toFixed(5)}, vGroundY);
  colour = mix(colour, mix(vec3(luma), colour, 0.35) * 0.72, rock * 0.8);

  // Snow lies flat and holds high: a face too steep to hold it stays the rock
  // the slope rule already made it.
  float snow = smoothstep(uSnowLow, uSnowHigh, vAltitude)
    * smoothstep(0.62, 0.84, vGroundY) * clamp(high, 0.0, 1.0);
  colour = mix(colour, vec3(0.90, 0.93, 0.97) * (0.86 + 0.28 * luma), snow);

  gTerrainNormal = surface;
  diffuseColor.rgb *= colour;
`,
    normal: `
  vec3 mapped = normalize(gTerrainNormal * 2.0 - 1.0);
  mapped = normalize(mix(vec3(0.0, 0.0, 1.0), mapped, gTerrainDetail));
  vec3 tangent = normalize(vTerrainTangent);
  normal = normalize(mat3(tangent, cross(normal, tangent), normal) * mapped);
`,
  };
}

function terrainMaterial(job, uniforms, count) {
  const chunks = terrainChunks(count);
  const material = new THREE.MeshStandardMaterial({ roughness: 1.0, metalness: 0.0 });
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>
uniform float uWorldSize;
varying vec2 vTerrainUv;
varying vec3 vTerrainTangent;
varying float vGroundY;
varying float vAltitude;`)
      .replace('#include <project_vertex>', `#include <project_vertex>
${chunks.vertex}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
${chunks.common}`)
      .replace('#include <map_fragment>', chunks.map)
      .replace('#include <normal_fragment_maps>', chunks.normal);
  };
  material.customProgramCacheKey = () => `terrain-${count}`;
  return material;
}

/* Every texture the terrain needs, and a promise per texture that the world is
 * not handed back before. An unloaded texture is not a blank one: three binds
 * an empty 1x1 in its place, which draws the whole ground BLACK — and a world
 * with no meshes to wait for renders its first frame right into that gap. */
function loadTexture(loader, pending, url, { data = false, repeat = false } = {}) {
  let texture;
  pending.push(new Promise((resolve) => {
    // a texture that 404s resolves too: a world missing one material is still a
    // world, and hanging the loader on it renders nothing at all
    texture = loader.load(url, resolve, undefined, resolve);
  }));
  if (data) {
    // Neither a weight nor a normal is a colour: decoded through an sRGB curve, a
    // weight stops meaning what the pipeline computed and a flat normal (128,128,255)
    // comes back as a constant 39-degree tilt, lighting the whole world as a slope.
    // flipY would mirror a weight map against the height field it was computed beside.
    texture.colorSpace = THREE.NoColorSpace;
    texture.flipY = repeat;
  } else {
    texture.colorSpace = THREE.SRGBColorSpace;
  }
  if (repeat) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  return texture;
}

/* A sky to reflect. The meshes arrive from reconstruction with metalness 1 and a
 * metalness map, and a metal with nothing to reflect is BLACK — which is how a
 * dark-timbered cottage rendered as a hole in the ground. This is the same sky
 * the terrain is lit by, as a gradient the materials can sample. */
function skyEnvironment() {
  const width = 64, height = 32;
  const data = new Uint8Array(width * height * 4);
  const sky = new THREE.Color(SKY_COLOUR);
  const ground = new THREE.Color(0x6a5c48);
  const colour = new THREE.Color();
  for (let y = 0; y < height; y++) {
    colour.copy(sky).lerp(ground, Math.min(Math.max((y / (height - 1) - 0.45) / 0.3, 0), 1));
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      data[i] = colour.r * 255; data[i + 1] = colour.g * 255; data[i + 2] = colour.b * 255;
      data[i + 3] = 255;
    }
  }
  const texture = new THREE.DataTexture(data, width, height);
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

class World {
  constructor(job, heights) {
    this.job = job;
    this.heights = heights;
    this.resolution = job.resolution;
    this.sizeM = job.size_m;
    this.group = new THREE.Group();
    this.colliders = [];
    this.regions = job.regions.map((r) => ({
      id: r.region_id,
      category: r.category,
      x: r.centre_m[0],
      z: r.centre_m[1],
      radius: r.radius_m,
    }));
  }

  /* Which region a point belongs to, by id. The regions overlap and blend, so
   * this answers with the nearest centre weighted by reach — the same thing the
   * ground under the point is mostly made of. */
  regionAt(x, z) {
    let best = null;
    let bestScore = -Infinity;
    for (const r of this.regions) {
      const score = r.radius - Math.hypot(x - r.x, z - r.z);
      if (score > bestScore) { bestScore = score; best = r; }
    }
    return best;
  }

  /* Height in metres under a world position, bilinear between samples. Off the
   * map returns the edge, so a game that walks out does not fall through. */
  heightAt(x, z) {
    const res = this.resolution;
    const u = Math.min(Math.max(x / this.sizeM, 0), 0.999999) * res;
    const v = Math.min(Math.max(z / this.sizeM, 0), 0.999999) * res;
    const x0 = Math.min(Math.floor(u), res - 1), z0 = Math.min(Math.floor(v), res - 1);
    const x1 = Math.min(x0 + 1, res - 1), z1 = Math.min(z0 + 1, res - 1);
    const fx = u - x0, fz = v - z0;
    const h = this.heights;
    const a = h[z0 * res + x0], b = h[z0 * res + x1];
    const c = h[z1 * res + x0], d = h[z1 * res + x1];
    return (a * (1 - fx) + b * fx) * (1 - fz) + (c * (1 - fx) + d * fx) * fz;
  }

  /* Ground normal, from the height field's own slope. */
  normalAt(x, z, step = 1.0) {
    const dx = this.heightAt(x + step, z) - this.heightAt(x - step, z);
    const dz = this.heightAt(x, z + step) - this.heightAt(x, z - step);
    return new THREE.Vector3(-dx, 2 * step, -dz).normalize();
  }

  /* The first collider a circle of `radius` at (x, z) overlaps, or null. Each is
   * a cylinder around a placed mesh — the cheapest shape that stops a player
   * walking through a church, and the one a game can also use for line of sight. */
  blocking(x, z, radius = 0.4) {
    for (const c of this.colliders) {
      const dx = x - c.x, dz = z - c.z;
      const reach = c.radius + radius;
      if (dx * dx + dz * dz < reach * reach) return c;
    }
    return null;
  }
}

function buildTerrain(job, heights, sample) {
  const grid = job.mesh_resolution;
  const res = job.resolution;
  const size = job.size_m;
  const side = grid + 1;
  const positions = new Float32Array(side * side * 3);
  const uvs = new Float32Array(side * side * 2);
  const indices = [];

  for (let z = 0; z < side; z++) {
    for (let x = 0; x < side; x++) {
      const u = x / grid, v = z / grid;
      const i = z * side + x;
      positions[i * 3] = u * size;
      // sampled the way heightAt samples it: a nearest tap here draws a surface the
      // player does not stand on, and every tree floats or sinks by the difference
      positions[i * 3 + 1] = sample(u * size, v * size);
      positions[i * 3 + 2] = v * size;
      uvs[i * 2] = u;
      uvs[i * 2 + 1] = v;
    }
  }
  for (let z = 0; z < grid; z++) {
    for (let x = 0; x < grid; x++) {
      const a = z * side + x, b = a + 1, c = a + side, d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

/* Every solid thing, from what the world SAYS it is rather than from a mesh.
 * A mesh is the last part of a world to arrive and the first a game can do
 * without: collision known only after a GLB loads is collision a game cannot
 * have while the art is still rendering. The stated height carries the width
 * too — these are things standing on the ground, near enough as wide as they
 * are tall, and a footprint that is approximate is what a cylinder already is. */
function readColliders(world) {
  for (const [key, group] of Object.entries(world.job.instances)) {
    for (const p of group.placements) {
      const metres = p.height_m * p.scale;
      // Only things a person could not walk over. Every collider is scanned for every
      // move, and a world scatters far more grass than it does trees.
      if (metres < COLLIDES_ABOVE_M) continue;
      world.colliders.push({
        key,
        x: p.position[0],
        z: p.position[2],
        radius: metres * 0.35,
        height: metres,
        ground: p.position[1],
      });
    }
  }
}

async function placeGroups(world, base, loader) {
  const gltf = new GLTFLoader();
  const environment = skyEnvironment();
  const entries = Object.entries(world.job.instances);
  await Promise.all(entries.map(([key, group]) => new Promise((resolve) => {
    gltf.load(base + group.mesh, (loaded) => {
      const box = new THREE.Box3().setFromObject(loaded.scene);
      const span = new THREE.Vector3();
      box.getSize(span);
      const tallest = Math.max(span.y, 0.0001);

      const sources = [];
      loaded.scene.updateMatrixWorld(true);
      loaded.scene.traverse((child) => {
        if (child.isMesh) sources.push(child);
      });

      for (const source of sources) {
        source.material.envMap = environment;
        source.material.envMapIntensity = 1.0;
        source.material.needsUpdate = true;
        const mesh = new THREE.InstancedMesh(
          source.geometry, source.material, group.placements.length,
        );
        mesh.castShadow = mesh.receiveShadow = true;
        const local = source.matrixWorld;
        const matrix = new THREE.Matrix4();
        group.placements.forEach((p, i) => {
          // The pipeline states a metre height, never a scale factor: a mesh
          // arrives at whatever size reconstruction gave it, and what is known
          // about it is how tall the thing itself should be.
          const factor = (p.height_m * p.scale) / tallest;
          matrix.compose(
            new THREE.Vector3(p.position[0], p.position[1] - box.min.y * factor, p.position[2]),
            new THREE.Quaternion().setFromAxisAngle(
              new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(p.yaw_deg),
            ),
            new THREE.Vector3(factor, factor, factor),
          );
          mesh.setMatrixAt(i, matrix.clone().multiply(local));
        });
        mesh.instanceMatrix.needsUpdate = true;
        world.group.add(mesh);
      }

      resolve();
    }, undefined, () => resolve());  // a mesh that fails to load simply is not there
  })));
}

/* Everything about drawing a world that lives on the renderer and the scene
 * rather than on a material. Shadows, tone mapping and fog are switched on ONCE
 * per canvas; a loader that does not get given them draws a world with no sun
 * cast on it and colours that clip wherever the sun is brightest. */
export function configureView(renderer, scene, world) {
  if (renderer) {
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    // The sun is a real sun: bright ground and a lit wall are both above 1.0 in
    // linear light, and clamping them is what flattened every sunlit slope to
    // the same paint. ACES rolls them off instead.
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = TONEMAP_EXPOSURE;
  }
  if (scene) {
    scene.fog = world.fog;
    if (!scene.background) scene.background = new THREE.Color(SKY_COLOUR);
  }
}

export async function loadWorld(url, { meshes = true, renderer = null, scene = null } = {}) {
  const base = url.slice(0, url.lastIndexOf('/') + 1);
  const job = await (await fetch(url)).json();
  const heights = new Float32Array(await (await fetch(base + job.heightmap)).arrayBuffer());
  const world = new World(job, heights);

  const loader = new THREE.TextureLoader();
  const pending = [];
  const count = job.regions.length;
  const uniforms = {
    uWeightsA: { value: loadTexture(loader, pending, base + job.weight_textures[0], { data: true }) },
    uWeightsB: {
      value: loadTexture(
        loader, pending, base + (job.weight_textures[1] || job.weight_textures[0]),
        { data: true },
      ),
    },
    uAlbedo: {
      value: job.regions.map((r) => loadTexture(loader, pending, base + r.albedo, { repeat: true })),
    },
    uNormal: {
      value: job.regions.map(
        (r) => loadTexture(loader, pending, base + r.normal, { data: true, repeat: true }),
      ),
    },
    uRepeats: { value: job.regions.map((r) => job.size_m / Math.max(r.scale_m, 0.01)) },
    // Which regions are high ground at all. Snow on the top of a lakebed is not
    // a threshold that was tuned wrong, it is snow in the wrong place.
    uHigh: { value: job.regions.map((r) => (HIGH_CATEGORIES.has(r.category) ? 1.0 : 0.0)) },
    uWorldSize: { value: job.size_m },
    uSnowLow: { value: 0.0 },
    uSnowHigh: { value: 0.0 },
    // Where the stochastic detail has stopped being detail and started being
    // noise, in metres. Relative to the world because that is what sets how far
    // away the ground is ever seen from.
    uDetailNear: { value: job.size_m * 0.25 },
    uDetailFar: { value: job.size_m * 0.9 },
    uSunDir: { value: new THREE.Vector3() },
  };

  // The snow line is the world's OWN relief, read off the height field rather
  // than stated anywhere: the job says how low the world goes and the buffer
  // says how high, and snow starts in the top quarter of the difference.
  let highest = -Infinity;
  for (let i = 0; i < heights.length; i++) if (heights[i] > highest) highest = heights[i];
  const relief = Math.max(highest - job.lowest_m, 0.001);
  uniforms.uSnowLow.value = job.lowest_m + relief * SNOW_RELIEF_START;
  uniforms.uSnowHigh.value = job.lowest_m + relief * SNOW_RELIEF_FULL;

  const elevation = THREE.MathUtils.degToRad(job.sun_elevation_deg);
  const azimuth = THREE.MathUtils.degToRad(job.sun_azimuth_deg);
  uniforms.uSunDir.value.set(
    Math.cos(elevation) * Math.sin(azimuth),
    Math.sin(elevation),
    Math.cos(elevation) * Math.cos(azimuth),
  ).normalize();

  const terrain = new THREE.Mesh(
    buildTerrain(job, heights, (x, z) => world.heightAt(x, z)),
    terrainMaterial(job, uniforms, count),
  );
  // Casting as well as receiving: a mountain that does not shade its own east
  // face at nine in the morning is a painted backdrop, and it is the single
  // largest shadow in any world.
  terrain.castShadow = terrain.receiveShadow = true;
  world.terrain = terrain;
  world.group.add(terrain);

  // Water is a single plane at sea level, and only where there is ground under
  // it to be flooded — a sea below the lowest point of the world is a sheet
  // hanging under the terrain that every low camera looks through.
  if (job.sea_level_m > job.lowest_m) {
    const water = new THREE.Mesh(
      new THREE.PlaneGeometry(job.size_m, job.size_m),
      // Unlit: a lit sheet either blows out to white under the world's ambient
      // or catches one specular blob where the sun is, and neither reads as
      // water from a camera the refinement loop did not choose.
      new THREE.MeshBasicMaterial({
        color: 0x21485c, transparent: true, opacity: 0.82, side: THREE.DoubleSide,
      }),
    );
    water.rotation.x = -Math.PI / 2;
    water.position.set(job.size_m * 0.5, job.sea_level_m, job.size_m * 0.5);
    world.water = water;
    world.group.add(water);
  }

  const sun = new THREE.DirectionalLight(SUN_COLOUR, SUN_ENERGY);
  const centre = new THREE.Vector3(job.size_m * 0.5, 0, job.size_m * 0.5);
  // Aimed at the middle of the world, not at the origin: a shadow camera hung
  // over the corner leaves everything past its edge clamped to the border of
  // the map, which draws whole villages in full shade under a midday sun.
  sun.target.position.copy(centre);
  sun.position.copy(centre).addScaledVector(uniforms.uSunDir.value, job.size_m);
  sun.castShadow = true;
  sun.shadow.mapSize.set(4096, 4096);
  const reach = job.size_m * 0.75;
  Object.assign(sun.shadow.camera, {
    left: -reach, right: reach, top: reach, bottom: -reach, near: 1, far: job.size_m * 3.0,
  });
  // A height field lit by its own coarse normals self-shadows in stripes unless
  // the lookup is pushed off the surface along it; in metres, because the texel
  // the shadow map covers is set by the size of the world.
  sun.shadow.normalBias = Math.max(job.size_m * 0.0025, 0.05);
  sun.shadow.bias = -0.00015;
  sun.shadow.camera.updateProjectionMatrix();
  world.group.add(sun.target);
  // The meshes are lit to the same floor as the terrain shader's ambient: a
  // sky light alone leaves every face turned away from the sun black, and a
  // dark-timbered cottage then reads as a hole in the ground.
  world.group.add(
    sun,
    new THREE.HemisphereLight(SKY_COLOUR, 0x6a5c48, HEMI_ENERGY),
    new THREE.AmbientLight(AMBIENT_COLOUR, AMBIENT_ENERGY),
  );
  world.sun = sun;
  // Aerial perspective, in the sky's own colour. A world with no fog reads as
  // a model on a table: the far ridge is exactly as saturated as the near one.
  // Beginning past the world's own width: anything further away than the whole
  // place is the distance that haze belongs at, and a nearer start fogs out the
  // very views the refinement loop is looking through.
  world.fog = new THREE.Fog(SKY_COLOUR, job.size_m * 0.9, job.size_m * 3.0);
  configureView(renderer, scene, world);

  readColliders(world);
  if (meshes) await placeGroups(world, base, loader);
  await Promise.all(pending);
  return world;
}
