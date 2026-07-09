import hashlib
import io
import math
import struct
import wave
from pathlib import Path


def write_silent_wav(path: Path, seconds: float = 0.4, rate: int = 22050) -> None:
    """Write a mono 16-bit silent WAV. The voice/music placeholder, so a clip that never generated
    still resolves at Ren'Py lint/runtime (mirrors write_solid_png for images)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)


def ambient_pad_bytes(seed: str, seconds: float = 12.0, rate: int = 22050) -> bytes:
    """A short, deterministic, license-free ambient pad as mono 16-bit PCM WAV bytes — the local
    `stub` music backend. A low root note (chosen from the seed) plus a fifth and octave, under a
    slow amplitude swell, with an edge fade so it loops without a click. Pure stdlib (no numpy):
    it proves the whole music pipeline end-to-end so a real model (MusicGen / stable-audio) is a
    drop-in behind the backend seam, not a rewrite."""
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    root = 110.0 * (2 ** ((h % 12) / 12.0))          # a low root in [110, 220) Hz
    partials = (1.0, 1.5, 2.0)                        # root, fifth, octave — a calm bed
    n = int(seconds * rate)
    fade = 1.5
    frames = bytearray()
    for i in range(n):
        t = i / rate
        swell = 0.5 + 0.5 * math.sin(2 * math.pi * 0.06 * t)
        s = sum(math.sin(2 * math.pi * root * m * t) / (k + 2)
                for k, m in enumerate(partials))
        env = min(1.0, t / fade, (seconds - t) / fade)
        val = int(max(-1.0, min(1.0, s * 0.28 * swell * env)) * 32767)
        frames += struct.pack("<h", val)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()
