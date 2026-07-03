"""Text-to-speech client for voice generation.

Talks to a local TTS HTTP server (OpenAI-compatible /v1/audio/speech, e.g. Kokoro-FastAPI /
an XTTS server). The renpy voice pass (renpy.fns.generate_voices) calls synthesize() per spoken
line; VRAM is freed for the duration by reusing comfyui_tools.vram_bracket (unloads the LLM /
frees SDXL so the TTS model has room). Mirrors comfyui_tools' shape: a thin client + a deterministic
job mapping, no engine knowledge.
"""

import logging
import urllib.request
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _tts_settings() -> Optional[dict]:
    from config.settings_manager import settings_manager
    return settings_manager.get_settings().get("tts")


def voice_enabled() -> bool:
    s = _tts_settings()
    return bool(s and s.get("endpoint"))


def pick_voice(char_id: str, voices: List[str], sex: Optional[str] = None,
               tts_voice: Optional[str] = None) -> Optional[str]:
    """Map a character onto one of the server's speaker presets. Precedence: an explicit
    tts_voice on the card wins; otherwise the bank is filtered by the card's sex before the
    deterministic hash — a male character must never hash onto a female voice (a sultry
    lounge-singer Danny is funnier than it is shippable). Kokoro encodes sex in the preset
    prefix (af_/bf_ female, am_/bm_ male); banks without that convention filter to nothing
    and fall back to the whole bank. No bank -> the server's default voice."""
    if tts_voice:
        return tts_voice
    if not voices:
        return None
    pool = voices
    if sex in ("male", "female"):
        want = "m" if sex == "male" else "f"
        filtered = [v for v in voices if len(v) > 1 and v[1] == want and v[2:3] == "_"]
        pool = filtered or voices
    return pool[sum(ord(c) for c in char_id) % len(pool)]


def synthesize(text: str, voice: Optional[str], timeout: int = 120) -> bytes:
    """POST one line to the local TTS server, return the audio bytes. Raises on failure; the
    caller (best-effort pass) catches and degrades to a silent placeholder."""
    s = _tts_settings() or {}
    endpoint = s.get("endpoint", "").rstrip("/")
    payload: Dict = {
        "input": text,
        "model": s.get("model", ""),
        "response_format": s.get("format", "wav"),
    }
    if voice:
        payload["voice"] = voice
    import json
    req = urllib.request.Request(
        f"{endpoint}/v1/audio/speech",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()
