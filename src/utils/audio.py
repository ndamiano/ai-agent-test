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


