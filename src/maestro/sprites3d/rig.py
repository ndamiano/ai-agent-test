"""A skeleton MEASURED onto a T-posed mesh, rather than inferred from its geometry.

A learned rigger reads limbs out of the surface, so props defeat it: measured on five characters,
one gave a knight 22 bones and NO limbs (his shield welds to his torso at 512^3), a mage in a
floor-length robe no legs, and a mounted rider no arms — 2 of 5 usable. But the pose is OURS. In a
T-pose the arms are horizontal at shoulder height and the legs hang below the hips, so a canonical
humanoid can be scaled to the silhouette and placed. Bones are correct by construction, carry
Mixamo names, and a knight behind a shield still has two arms.

Everything is in glTF's own frame: X lateral, Y up, Z depth. Joints are placed with no rotation,
so a joint's bind matrix is a pure translation and a pose is a rotation about its own head — which
is what makes the retarget's arithmetic simple.
"""
from typing import Dict, List, Tuple

import numpy as np

from .gltf import Builder, Glb, matrix_of

# A vertex takes its weight from the bones it is nearest. Crude beside heat diffusion and adequate
# at 128px: rendered side by side against a learned rigger's heat weights, the difference did not
# survive the sprite's own resolution.
NEAREST_BONES = 4
FALLOFF = 4.0
UP, LATERAL, DEPTH = 1, 0, 2


def _span_at(verts: np.ndarray, height_frac: float, tol: float = 0.03) -> float:
    """How wide the body is at a fraction of its height. In a T-pose the arms are the widest
    band and the hips the narrowest below the ribs, so both can be read off rather than assumed."""
    lo, hi = verts[:, UP].min(), verts[:, UP].max()
    height = hi - lo
    at = lo + height * height_frac
    band = verts[np.abs(verts[:, UP] - at) < height * tol]
    return float(band[:, LATERAL].max() - band[:, LATERAL].min()) if len(band) else 0.0


def measure(verts: np.ndarray) -> Dict:
    lo, hi = float(verts[:, UP].min()), float(verts[:, UP].max())
    return {"height": hi - lo, "floor": lo,
            "arm_span": _span_at(verts, 0.80), "hip_width": _span_at(verts, 0.52),
            "depth": float(verts[:, DEPTH].max() - verts[:, DEPTH].min()),
            "centre_x": float((verts[:, LATERAL].min() + verts[:, LATERAL].max()) / 2.0),
            "centre_z": float(verts[:, DEPTH].mean())}


def humanoid(m: Dict) -> bool:
    """Whether a T-posed silhouette is a humanoid at all — a skeleton placed on anything else is
    named nonsense, and the caller draws that sheet instead of shipping a mislabelled rig.

    DEPTH is what separates them, not width: measured across five characters, a person is
    0.23-0.43 as deep as they are tall whatever they wear, while a horse and rider is 0.94. Arm
    span alone passes the horse at 0.89, because a rider's arms are still arms."""
    h = m["height"]
    if h <= 0:
        return False
    return (0.35 <= m["arm_span"] / h <= 1.6
            and 0.05 <= m["hip_width"] / h <= 0.8
            and m["depth"] / h <= 0.6)


def skeleton(m: Dict) -> List[Tuple[str, str, Tuple[float, float, float]]]:
    """(name, parent, head) for a canonical humanoid scaled onto these measurements."""
    h, floor = m["height"], m["floor"]
    cx, cz = m["centre_x"], m["centre_z"]
    shoulder_y = floor + h * 0.80
    hip_y = floor + h * 0.52
    half_arm = max(m["arm_span"] / 2.0, h * 0.18)
    hip_off = max(m["hip_width"] * 0.25, h * 0.04)

    bones = [
        ("Hips", None, (cx, hip_y, cz)),
        ("Spine", "Hips", (cx, hip_y + h * 0.09, cz)),
        ("Spine1", "Spine", (cx, hip_y + h * 0.18, cz)),
        ("Spine2", "Spine1", (cx, shoulder_y, cz)),
        ("Neck", "Spine2", (cx, shoulder_y + h * 0.05, cz)),
        ("Head", "Neck", (cx, shoulder_y + h * 0.10, cz)),
        ("HeadTop", "Head", (cx, floor + h, cz)),
    ]
    for side, s in (("Left", -1.0), ("Right", 1.0)):
        bones += [
            (f"{side}Shoulder", "Spine2", (cx + s * h * 0.05, shoulder_y, cz)),
            (f"{side}Arm", f"{side}Shoulder", (cx + s * half_arm * 0.32, shoulder_y, cz)),
            (f"{side}ForeArm", f"{side}Arm", (cx + s * half_arm * 0.66, shoulder_y, cz)),
            (f"{side}Hand", f"{side}ForeArm", (cx + s * half_arm * 0.92, shoulder_y, cz)),
            (f"{side}HandEnd", f"{side}Hand", (cx + s * half_arm, shoulder_y, cz)),
            (f"{side}UpLeg", "Hips", (cx + s * hip_off, hip_y, cz)),
            (f"{side}Leg", f"{side}UpLeg", (cx + s * hip_off, floor + h * 0.28, cz)),
            (f"{side}Foot", f"{side}Leg", (cx + s * hip_off, floor + h * 0.04, cz)),
            (f"{side}ToeBase", f"{side}Foot", (cx + s * hip_off, floor, cz - h * 0.05)),
        ]
    return bones


def _tails(bones) -> Dict[str, np.ndarray]:
    """Where each bone points: at its first child, or a stub for a leaf — the segment a vertex
    measures its distance to."""
    heads = {n: np.array(p, dtype=np.float64) for n, _, p in bones}
    first_child = {}
    for n, parent, _ in bones:
        if parent and parent not in first_child:
            first_child[parent] = n
    out = {}
    for n, _, _ in bones:
        child = first_child.get(n)
        tail = heads[child] if child else heads[n] + np.array([0.0, 0.02, 0.0])
        if np.linalg.norm(tail - heads[n]) < 1e-6:
            tail = heads[n] + np.array([0.0, 0.02, 0.0])
        out[n] = tail
    return out


def skin_weights(verts: np.ndarray, bones, heads, tails, eps: float) -> Tuple[np.ndarray, np.ndarray]:
    """(joint indices, weights) per vertex, by distance to each bone's segment."""
    names = [n for n, _, _ in bones]
    d = np.empty((len(verts), len(names)))
    for i, n in enumerate(names):
        head, tail = heads[n], tails[n]
        seg = tail - head
        length2 = float(seg @ seg) or 1e-9
        u = np.clip(((verts - head) @ seg) / length2, 0.0, 1.0)[:, None]
        d[:, i] = np.linalg.norm(verts - (head + u * seg), axis=1)
    order = np.argsort(d, axis=1)[:, :NEAREST_BONES]
    picked = np.take_along_axis(d, order, axis=1) + eps
    w = 1.0 / picked ** FALLOFF
    return order.astype(np.uint16), (w / w.sum(axis=1, keepdims=True)).astype(np.float32)


def rig_mesh(mesh_glb: str, out_glb: str) -> Dict:
    """Place the skeleton on a T-posed mesh, skin it, and write the rigged glb."""
    glb = Glb(mesh_glb)
    verts = glb.positions()
    m = measure(verts)
    if not humanoid(m):
        raise ValueError(f"not a humanoid: {m}")

    bones = skeleton(m)
    heads = {n: np.array(p, dtype=np.float64) for n, _, p in bones}
    tails = _tails(bones)
    joints, weights = skin_weights(verts, bones, heads, tails, m["height"] * 0.02)

    build = Builder(glb)
    nodes = build.json.setdefault("nodes", [])
    index_of = {}
    for name, _, head in bones:
        index_of[name] = len(nodes)
        nodes.append({"name": name, "translation": [0.0, 0.0, 0.0], "rotation": [0, 0, 0, 1]})
    for name, parent, head in bones:
        node = nodes[index_of[name]]
        origin = heads[parent] if parent else np.zeros(3)
        node["translation"] = [float(x) for x in (heads[name] - origin)]
        if parent:
            nodes[index_of[parent]].setdefault("children", []).append(index_of[name])

    inverse_binds = np.stack([np.linalg.inv(matrix_of(heads[n], np.array([0, 0, 0, 1.0])))
                              for n, _, _ in bones])
    # glTF matrices are column-major
    ibm = build.add(np.ascontiguousarray(inverse_binds.transpose(0, 2, 1)).reshape(-1, 16), "MAT4")
    skin = {"joints": [index_of[n] for n, _, _ in bones], "inverseBindMatrices": ibm,
            "skeleton": index_of["Hips"]}
    build.json.setdefault("skins", []).append(skin)

    prim = build.json["meshes"][0]["primitives"][0]
    prim["attributes"]["JOINTS_0"] = build.add(joints, "VEC4", component=5123)
    prim["attributes"]["WEIGHTS_0"] = build.add(weights, "VEC4")

    mesh_nodes = [n for n in nodes if n.get("mesh") is not None]
    if not mesh_nodes:
        raise ValueError("no node draws the mesh")
    mesh_nodes[0]["skin"] = len(build.json["skins"]) - 1
    # a skinned mesh is posed by its joints, so its own transform must not move it again
    for key in ("translation", "rotation", "scale", "matrix"):
        mesh_nodes[0].pop(key, None)
    scene = build.json["scenes"][build.json.get("scene", 0)]
    if index_of["Hips"] not in scene.setdefault("nodes", []):
        scene["nodes"].append(index_of["Hips"])

    build.write(out_glb)
    return {**m, "bones": len(bones)}
