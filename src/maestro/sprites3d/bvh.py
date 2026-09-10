"""A BVH motion clip, read without Blender.

The format is a skeleton of named joints with a rest OFFSET each, then one line per frame of
channel values — rotations in the order the header names, and a translation on the root. Nothing
here needs an application: the parse is a dozen lines and the kinematics are matrix products.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

_AXIS = {"Xrotation": 0, "Yrotation": 1, "Zrotation": 2}


def _rotation(axis: int, degrees: float) -> np.ndarray:
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    m = np.eye(4)
    if axis == 0:
        m[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
    elif axis == 1:
        m[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    else:
        m[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return m


@dataclass
class Clip:
    names: List[str]
    parents: List[int]
    offsets: np.ndarray                     # (J, 3) rest offset from the parent
    channels: List[List[str]]
    frames: np.ndarray                      # (F, total channels)
    frame_time: float
    _index: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self._index = {n: i for i, n in enumerate(self.names)}

    @property
    def fps(self) -> int:
        return int(round(1.0 / self.frame_time)) if self.frame_time else 30

    def index(self, name: str) -> Optional[int]:
        return self._index.get(name)

    def world(self, frame: int) -> np.ndarray:
        """(J, 4, 4) world matrices for one frame, parents before children."""
        out = np.zeros((len(self.names), 4, 4))
        cursor = 0
        for j, chans in enumerate(self.channels):
            local = np.eye(4)
            local[:3, 3] = self.offsets[j]
            for name in chans:
                value = self.frames[frame, cursor]
                cursor += 1
                if name in _AXIS:
                    local = local @ _rotation(_AXIS[name], value)
                elif name == "Xposition":
                    local[0, 3] = value
                elif name == "Yposition":
                    local[1, 3] = value
                elif name == "Zposition":
                    local[2, 3] = value
            parent = self.parents[j]
            out[j] = local if parent < 0 else out[parent] @ local
        return out


def load(path) -> Clip:
    tokens = Path(path).read_text().split()
    i = 0
    names, parents, offsets, channels = [], [], [], []
    stack: List[int] = []
    while tokens[i] != "MOTION":
        word = tokens[i]
        if word in ("ROOT", "JOINT"):
            names.append(tokens[i + 1])
            parents.append(stack[-1] if stack else -1)
            offsets.append([0.0, 0.0, 0.0])
            channels.append([])
            stack.append(len(names) - 1)
            i += 2
        elif word == "End":
            # an End Site has an offset and no channels; it names no joint a clip can drive
            depth = 0
            while True:
                if tokens[i] == "{":
                    depth += 1
                elif tokens[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            i += 1
        elif word == "OFFSET":
            offsets[stack[-1]] = [float(x) for x in tokens[i + 1:i + 4]]
            i += 4
        elif word == "CHANNELS":
            count = int(tokens[i + 1])
            channels[stack[-1]] = tokens[i + 2:i + 2 + count]
            i += 2 + count
        elif word == "}":
            stack.pop()
            i += 1
        else:
            i += 1
    frame_count = int(tokens[tokens.index("Frames:") + 1])
    frame_time = float(tokens[tokens.index("Time:") + 1])
    start = tokens.index("Time:") + 2
    width = sum(len(c) for c in channels)
    values = np.array(tokens[start:start + frame_count * width], dtype=np.float64)
    return Clip(names=names, parents=parents, offsets=np.array(offsets), channels=channels,
                frames=values.reshape(frame_count, width), frame_time=frame_time)
