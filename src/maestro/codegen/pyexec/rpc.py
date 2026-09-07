"""The wire between a confined program and the tools it may call.

Length-prefixed JSON over one socket, one call in flight at a time — the program is single
threaded and blocks on every call, so nothing needs to be interleaved.
"""

from __future__ import annotations

import json
import socket
import struct

_HEADER = struct.Struct("!I")
MAX_FRAME = 64 * 1024 * 1024      # a write_file body is the largest thing that crosses


def send(sock: socket.socket, obj) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ValueError(f"frame of {len(body)} bytes is over the {MAX_FRAME} limit")
    sock.sendall(_HEADER.pack(len(body)) + body)


def recv(sock: socket.socket):
    """The next frame, or None when the peer is gone — a closed socket is how the child's death
    reaches the parent's serve loop."""
    head = _recv_exactly(sock, _HEADER.size)
    if head is None:
        return None
    (size,) = _HEADER.unpack(head)
    if size > MAX_FRAME:
        raise ValueError(f"peer announced a {size} byte frame")
    body = _recv_exactly(sock, size)
    return None if body is None else json.loads(body)


def _recv_exactly(sock: socket.socket, n: int):
    chunks, got = [], 0
    while got < n:
        chunk = sock.recv(min(n - got, 1 << 20))
        if not chunk:
            return None
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)
