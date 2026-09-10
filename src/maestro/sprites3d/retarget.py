"""A motion clip moved onto a rig we placed.

A rotation in a clip is relative to the SOURCE skeleton's rest, so what carries across is the
DELTA from that rest, in world space:

    delta      = src_pose_world @ src_rest_world^-1
    tgt_world  = delta @ tgt_rest_world

Everything the source cannot know — where our arms point — comes from the target's own rest. Our
joints are placed with no rotation, so a target's rest rotation is the identity and its world
rotation IS the delta; the local rotation written to the glb is that against its parent's.

Four things all have to be right, and each was its own wrong result:

  * the source reference is a standard T-POSE clip, not the skeleton's rest offsets, which are the
    raw stick figure and double-rotate an already-T-posed target;
  * left and right are crossed — the mocap skeleton's left arm points the opposite way to ours, so
    a world rotation that lowers theirs raises ours;
  * an in-place sprite needs root travel AND root yaw stripped, or a curved clip walks out of its
    own facing;
  * the retarget preserves the HIPS' height, not the feet's, so a skeleton with different leg
    lengths floats or sinks unless the lower foot is planted each frame.
"""
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import bvh
from .gltf import Builder, Glb, normalise, quaternion_of

# SOMA (the mocap skeleton) -> the names `rig.skeleton` places. The sides are crossed here rather
# than mirroring every rotation downstream.
BONE_MAP = {
    "Hips": "Hips", "Spine1": "Spine", "Spine2": "Spine1", "Chest": "Spine2",
    "Neck1": "Neck", "Neck2": "Head",
    "LeftShoulder": "RightShoulder", "LeftArm": "RightArm",
    "LeftForeArm": "RightForeArm", "LeftHand": "RightHand",
    "LeftLeg": "RightUpLeg", "LeftShin": "RightLeg", "LeftFoot": "RightFoot",
    "LeftToeBase": "RightToeBase",
    "RightShoulder": "LeftShoulder", "RightArm": "LeftArm",
    "RightForeArm": "LeftForeArm", "RightHand": "LeftHand",
    "RightLeg": "LeftUpLeg", "RightShin": "LeftLeg", "RightFoot": "LeftFoot",
    "RightToeBase": "LeftToeBase",
}
FEET = ("LeftToeBase", "RightToeBase", "LeftFoot", "RightFoot")


def _target_rig(glb: Glb) -> Tuple[List[str], Dict[str, int], Dict[str, Optional[str]], Dict[str, np.ndarray]]:
    skin = glb.json["skins"][0]
    nodes = glb.json["nodes"]
    names, parent_of, local = [], {}, {}
    for j in skin["joints"]:
        names.append(nodes[j].get("name"))
    index_of = {n: i for i, n in enumerate(names)}
    joint_at = {j: nodes[j].get("name") for j in skin["joints"]}
    for j in skin["joints"]:
        for child in nodes[j].get("children", []):
            if child in joint_at:
                parent_of[joint_at[child]] = joint_at[j]
    for n in names:
        parent_of.setdefault(n, None)
        local[n] = np.array(nodes[skin["joints"][index_of[n]]].get("translation", [0, 0, 0]),
                            dtype=np.float64)
    return names, index_of, parent_of, local


def _order(names, parent_of) -> List[str]:
    """Parents first — a child's world matrix needs its parent's."""
    done, out = set(), []
    while len(out) < len(names):
        for n in names:
            if n not in done and (parent_of[n] is None or parent_of[n] in done):
                out.append(n)
                done.add(n)
    return out


def cycle_length(rotations: np.ndarray) -> Dict:
    """The shift that makes the pose repeat, when one does — so a walk's sheet samples a whole
    stride rather than an arbitrary slice.

    A shorter shift compares fewer, more similar frames, so raw error alone always prefers the
    smallest one it is offered and calls a 60-frame attack a 5-frame loop. Two things separate a
    real cycle from that: the error is judged against how much the clip MOVES over the span, and
    a genuine repeat lands on a plausible stride rather than on the smallest shift searched —
    measured, every one-shot's best match sat exactly on that floor (attack, death and dodge all
    at 5) while the walks sat at 35 and 44."""
    frames = len(rotations)
    if frames < 12:
        return {}
    floor = max(4, frames // 12)
    best = None
    for shift in range(floor, frames // 2):
        a, b = rotations[:frames - shift], rotations[shift:]
        err = float(np.minimum(np.abs(a - b).sum(axis=2), np.abs(a + b).sum(axis=2)).mean())
        spread = float(np.abs(a - a.mean(axis=0, keepdims=True)).sum(axis=2).mean()) or 1e-9
        score = err / spread
        if best is None or score < best[0]:
            best = (score, shift, err)
    if best is None or best[1] <= floor or best[0] > 0.7:
        # nothing repeats: a one-shot is played once through, not looped
        return {"cycle_frames": frames, "cycle_error": None, "loops": False}
    return {"cycle_frames": best[1], "cycle_error": round(best[2], 4), "loops": True}


def retarget(rigged_glb: str, clip_path: str, out_glb: str, ref_bvh: str,
             in_place: bool = True, max_frames: Optional[int] = None) -> Dict:
    glb = Glb(rigged_glb)
    names, index_of, parent_of, rest_local = _target_rig(glb)
    order = _order(names, parent_of)

    clip = bvh.load(clip_path)
    reference = bvh.load(ref_bvh)
    pairs = [(s, t) for s, t in BONE_MAP.items()
             if clip.index(s) is not None and t in index_of]
    if len(pairs) < 8:
        raise ValueError(f"only {len(pairs)} joints mapped — this clip does not fit the rig")

    ref_world = reference.world(0)
    src_reference = {t: ref_world[reference.index(s)][:3, :3] for s, t in pairs
                     if reference.index(s) is not None}
    driven = {t for _, t in pairs if t in src_reference}

    rest_world = {}
    for n in order:
        p = parent_of[n]
        rest_world[n] = rest_local[n] + (rest_world[p] if p else 0.0)

    frame_count = len(clip.frames) if not max_frames else min(len(clip.frames), max_frames)
    scale = _scale(rest_world, reference)
    src_hips_rest = ref_world[reference.index([s for s, t in pairs if t == "Hips"][0])][:3, 3]

    quats = np.zeros((frame_count, len(names), 4))
    hips = np.zeros((frame_count, 3))
    ground = None
    for f in range(frame_count):
        src = clip.world(f)
        world_rot, local_q = {}, {}
        for n in order:
            source = next((s for s, t in pairs if t == n), None)
            if n in driven and source is not None:
                world_rot[n] = src[clip.index(source)][:3, :3] @ np.linalg.inv(src_reference[n])
            else:
                world_rot[n] = world_rot[parent_of[n]] if parent_of[n] else np.eye(3)
            if n == "Hips" and in_place:
                world_rot[n] = _without_yaw(world_rot[n])
            parent_rot = world_rot[parent_of[n]] if parent_of[n] else np.eye(3)
            local = np.linalg.inv(parent_rot) @ world_rot[n]
            m = np.eye(4)
            m[:3, :3] = local
            local_q[n] = quaternion_of(m)
            quats[f, index_of[n]] = local_q[n]

        travel = (src[clip.index("Hips")][:3, 3] - src_hips_rest) * scale
        if in_place:
            travel[0] = travel[2] = 0.0
        position = rest_world["Hips"] + travel
        low = _lowest_foot(order, parent_of, rest_local, world_rot, position, rest_world, index_of)
        if ground is None:
            ground = low
        position[1] += ground - low
        hips[f] = position - (rest_world["Hips"] - rest_local["Hips"])

    info = {"frames": frame_count, "fps": clip.fps, "mapped": len(pairs)}
    info.update(cycle_length(quats[:, [index_of[t] for t in sorted(driven)]]))
    _write(glb, out_glb, names, index_of, quats, hips, clip.fps)
    return info


def _scale(rest_world: Dict[str, np.ndarray], reference: bvh.Clip) -> float:
    ours = max(p[1] for p in rest_world.values()) - min(p[1] for p in rest_world.values())
    theirs = reference.world(0)[:, 1, 3]
    theirs = float(theirs.max() - theirs.min()) or 1.0
    return ours / theirs


def _without_yaw(rot: np.ndarray) -> np.ndarray:
    """A sprite faces the way its sheet says, so a clip's turn is dropped and its lean kept."""
    forward = rot @ np.array([0.0, 0.0, 1.0])
    yaw = np.arctan2(forward[0], forward[2])
    c, s = np.cos(-yaw), np.sin(-yaw)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]) @ rot


def _lowest_foot(order, parent_of, rest_local, world_rot, hips_position, rest_world,
                 index_of) -> float:
    world = {}
    for n in order:
        p = parent_of[n]
        if p is None:
            world[n] = hips_position
        else:
            world[n] = world[p] + world_rot[p] @ rest_local[n]
    feet = [world[n][1] for n in FEET if n in world]
    return min(feet) if feet else 0.0


def _write(glb: Glb, out_glb: str, names, index_of, quats, hips, fps) -> None:
    build = Builder(glb)
    joints = build.json["skins"][0]["joints"]
    times = (np.arange(len(quats)) / float(fps)).astype(np.float32).reshape(-1, 1)
    time_acc = build.add(times, "SCALAR", minmax=True)
    channels, samplers = [], []
    for name in names:
        track = normalise(quats[:, index_of[name]]).astype(np.float32)
        acc = build.add(track, "VEC4")
        samplers.append({"input": time_acc, "output": acc, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": joints[index_of[name]], "path": "rotation"}})
    hips_acc = build.add(hips.astype(np.float32), "VEC3")
    samplers.append({"input": time_acc, "output": hips_acc, "interpolation": "LINEAR"})
    channels.append({"sampler": len(samplers) - 1,
                     "target": {"node": joints[index_of["Hips"]], "path": "translation"}})
    build.json.setdefault("animations", []).append(
        {"name": "clip", "channels": channels, "samplers": samplers})
    build.write(out_glb)
