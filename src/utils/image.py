import struct
import zlib
from pathlib import Path


def write_solid_png(path: Path, width: int, height: int, rgb: tuple) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row  = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height, level=1)
    png  = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
