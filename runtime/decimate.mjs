// decimate.mjs — shrink a TRELLIS GLB to game weight. TRELLIS emits ~500k triangles per object
// (~16MB, 99% geometry; textures are already webp) — far past what a low-poly village needs.
// Weld + meshopt-simplify toward a triangle budget, then quantize attributes
// (KHR_mesh_quantization — three's GLTFLoader decodes it natively, no decoder wasm needed).
// Foliage-like meshes (thousands of disconnected leaf shells) resist topological collapse — for
// those, fall back to meshopt's SLOPPY simplifier, which ignores topology and hits the budget.
//   node decimate.mjs <in.glb> <out.glb> [maxTris=20000]
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { weld, simplify, quantize, prune, dedup } from "@gltf-transform/functions";
import { MeshoptSimplifier } from "meshoptimizer";

const [, , inPath, outPath, maxTrisArg] = process.argv;
const maxTris = Number(maxTrisArg) || 20000;

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const doc = await io.read(inPath);

const countTris = () => {
  let tris = 0;
  for (const mesh of doc.getRoot().listMeshes())
    for (const prim of mesh.listPrimitives()) {
      const idx = prim.getIndices();
      tris += (idx ? idx.getCount() : prim.getAttribute("POSITION").getCount()) / 3;
    }
  return tris;
};

const before = countTris();
const ratio = Math.min(1, maxTris / Math.max(1, before));
// error stays SMALL here — unbounded collapse tears buildings apart; anything this pass can't
// bring near budget (foliage: thousands of disconnected leaf shells) drops to the sloppy pass.
await doc.transform(dedup(), weld(), simplify({ simplifier: MeshoptSimplifier, ratio, error: 0.05 }));

// The gentle pass leaves structured meshes (buildings, creatures) a little over budget but INTACT
// — sloppy would tear their walls. Only true foliage-class geometry (stalls out at 10x budget:
// thousands of disconnected leaf shells) drops to the topology-ignoring pass.
let sloppy = false;
if (countTris() > maxTris * 5) {
  sloppy = true;
  await MeshoptSimplifier.ready;
  const total = countTris();
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) {
      const idx = prim.getIndices();
      const pos = prim.getAttribute("POSITION");
      if (!idx || !pos) continue;
      const primTris = idx.getCount() / 3;
      const target = Math.min(idx.getCount(), Math.max(96, Math.round(maxTris * primTris / total)) * 3);
      const positions = new Float32Array(pos.getArray());
      const indices = new Uint32Array(idx.getArray());
      const [newIndices] = MeshoptSimplifier.simplifySloppy(indices, positions, 3, null, target, 1);
      idx.setArray(newIndices);
    }
  }
}

await doc.transform(quantize(), prune());
await io.write(outPath, doc);
console.log(JSON.stringify({ before, after: countTris(), sloppy }));
