"""Reading and writing the glb, without Blender.

Blender's Python API is the whole application as a module and carries its GPL — too heavy a
licence to sit in the middle of a commercial pipeline for what amounts to file I/O and matrix
arithmetic. Everything needed here is a few accessors: the mesh's positions, a skin (joints,
weights, inverse bind matrices) and an animation's rotation tracks.
"""
import struct
from pathlib import Path
from typing import Dict, Optional

import numpy as np

COMPONENT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
FLOAT, USHORT = 5126, 5123


class Glb:
    """A parsed .glb: its json and its single binary chunk."""

    def __init__(self, path=None, data: bytes = None):
        raw = Path(path).read_bytes() if path is not None else data
        if raw[:4] != b"glTF":
            raise ValueError("not a glb")
        json_len = struct.unpack("<I", raw[12:16])[0]
        import json as _json
        self.json = _json.loads(raw[20:20 + json_len])
        rest = raw[20 + json_len:]
        self.bin = b""
        if len(rest) >= 8:
            bin_len = struct.unpack("<I", rest[0:4])[0]
            self.bin = rest[8:8 + bin_len]

    def read(self, accessor_index: int) -> np.ndarray:
        acc = self.json["accessors"][accessor_index]
        view = self.json["bufferViews"][acc["bufferView"]]
        width = WIDTH[acc["type"]]
        fmt = COMPONENT[acc["componentType"]]
        item = struct.calcsize("<" + fmt)
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride") or width * item
        out = np.empty((acc["count"], width), dtype=np.dtype(fmt))
        for i in range(acc["count"]):
            off = start + i * stride
            out[i] = struct.unpack_from("<" + fmt * width, self.bin, off)
        return out

    def positions(self) -> np.ndarray:
        prim = self.json["meshes"][0]["primitives"][0]
        return self.read(prim["attributes"]["POSITION"]).astype(np.float64)

    def node_index(self, name: str) -> Optional[int]:
        for i, node in enumerate(self.json.get("nodes", [])):
            if node.get("name") == name:
                return i
        return None


def _pad(data: bytes, to: int = 4, fill: bytes = b"\x00") -> bytes:
    return data + fill * (-len(data) % to)


class Builder:
    """Appends accessors to an existing glb's binary chunk, so the mesh, its material and its
    texture survive untouched — only the skin and the animation are new."""

    def __init__(self, glb: Glb):
        self.json = glb.json
        self.blob = bytearray(glb.bin)
        self.json.setdefault("bufferViews", [])
        self.json.setdefault("accessors", [])

    def add(self, array: np.ndarray, kind: str, component: int = FLOAT,
            minmax: bool = False) -> int:
        fmt = COMPONENT[component]
        flat = array.reshape(-1).astype(np.dtype(fmt))
        offset = len(self.blob)
        self.blob.extend(flat.tobytes())
        self.blob.extend(b"\x00" * (-len(self.blob) % 4))
        self.json["bufferViews"].append({"buffer": 0, "byteOffset": offset,
                                         "byteLength": int(flat.nbytes)})
        acc = {"bufferView": len(self.json["bufferViews"]) - 1, "componentType": component,
               "count": int(array.shape[0]), "type": kind}
        if minmax:
            a = array.reshape(array.shape[0], -1)
            acc["min"] = [float(x) for x in a.min(axis=0)]
            acc["max"] = [float(x) for x in a.max(axis=0)]
        self.json["accessors"].append(acc)
        return len(self.json["accessors"]) - 1

    def write(self, path) -> Path:
        import json as _json
        self.json["buffers"] = [{"byteLength": len(self.blob)}]
        js = _pad(_json.dumps(self.json, separators=(",", ":")).encode(), 4, b" ")
        binary = _pad(bytes(self.blob))
        out = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js) + 8 + len(binary))
        out += struct.pack("<I", len(js)) + b"JSON" + js
        out += struct.pack("<I", len(binary)) + b"BIN\x00" + binary
        Path(path).write_bytes(out)
        return Path(path)


def trs(translation=(0, 0, 0), rotation=(0, 0, 0, 1)) -> Dict:
    return {"translation": [float(x) for x in translation],
            "rotation": [float(x) for x in rotation]}


def matrix_of(translation: np.ndarray, quaternion: np.ndarray) -> np.ndarray:
    """A 4x4 from a translation and a glTF quaternion (x, y, z, w)."""
    x, y, z, w = quaternion
    m = np.eye(4)
    m[:3, :3] = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    m[:3, 3] = translation
    return m


def quaternion_of(m: np.ndarray) -> np.ndarray:
    """The glTF quaternion (x, y, z, w) of a rotation matrix."""
    r = m[:3, :3]
    trace = np.trace(r)
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q, dtype=np.float64)
    return q / np.linalg.norm(q)


def normalise(q: np.ndarray) -> np.ndarray:
    return q / (np.linalg.norm(q, axis=-1, keepdims=True) + 1e-12)
