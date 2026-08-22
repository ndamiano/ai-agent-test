// lib/lights.js — lighting for a three.js scene in one call, so nothing renders near-black.
//
//   import * as THREE from './three.module.js';
//   import { lightScene } from './lib/lights.js';
//   const lights = lightScene(renderer, scene);                    // sun + sky light + tone mapping, shadows on
//   const lights = lightScene(renderer, scene, { indoor: true });  // warm ceiling light + soft fill, darker ambience
//   lightScene(renderer, scene, { sunFrom: [50, 80, 30], intensity: 2.5, exposure: 1.0, shadowSize: 60 });
//
//   lights.sun        the THREE.DirectionalLight (casts shadows over `shadowSize` metres around the origin)
//   lights.hemi       the THREE.HemisphereLight
//   lights.follow(x, z)   keep the shadow area centred on the player in a large scene; call each frame
//
// Shadows: renderer.shadowMap is enabled here; set `mesh.castShadow = true` on things that should
// cast and `receiveShadow = true` on the floor, nothing else is needed. Use MeshStandardMaterial
// (or Lambert/Phong) for lit surfaces — MeshBasicMaterial ignores lights entirely.
//
// Do NOT call this for an outdoor world loaded with world.js: loadWorld sets its own sun, sky and
// exposure and a second sun would double-light the terrain. Use it for indoor scenes, dungeons,
// arenas, vehicles and any 3D scene that did not come from compose_world.

import * as THREE from '../three.module.js';

export function lightScene(renderer, scene, {
  indoor = false, sunFrom = indoor ? [10, 30, 10] : [60, 100, 40], intensity = indoor ? 1.6 : 2.8,
  exposure = indoor ? 1.1 : 1.0, shadowSize = 60, sky = indoor ? 0x8a7a6a : 0x9ec5ff,
  ground = indoor ? 0x2a2420 : 0x5a5140,
} = {}) {
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = exposure;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const sun = new THREE.DirectionalLight(indoor ? 0xffe4c4 : 0xfff2dc, intensity);
  sun.position.set(sunFrom[0], sunFrom[1], sunFrom[2]);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.near = 0.5;
  sun.shadow.camera.far = 400;
  sun.shadow.camera.left = -shadowSize; sun.shadow.camera.right = shadowSize;
  sun.shadow.camera.top = shadowSize; sun.shadow.camera.bottom = -shadowSize;
  sun.shadow.bias = -0.0005;
  sun.shadow.normalBias = 0.02;
  scene.add(sun);
  scene.add(sun.target);

  const hemi = new THREE.HemisphereLight(sky, ground, indoor ? 0.8 : 1.2);
  scene.add(hemi);

  const fill = new THREE.AmbientLight(0xffffff, indoor ? 0.25 : 0.15);
  scene.add(fill);

  if (!scene.background) scene.background = new THREE.Color(indoor ? 0x14121a : 0x87b8e8);

  const offset = new THREE.Vector3(sunFrom[0], sunFrom[1], sunFrom[2]);
  return {
    sun, hemi, fill,
    follow(x, z) {
      sun.target.position.set(x, 0, z);
      sun.position.set(x + offset.x, offset.y, z + offset.z);
      sun.target.updateMatrixWorld();
    },
  };
}
