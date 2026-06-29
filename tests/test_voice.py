"""Voice generation: filename derivation, projection, placeholder backfill, gating."""
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.ir_assemble import voice_file, voiced_lines
from renpy.ir_vn import compile_vn
from tools.tts_tools import pick_voice

_EXAMPLE = Path(__file__).parent.parent / "docs" / "examples" / "vn_crappy.json"


def _ir():
    return json.loads(_EXAMPLE.read_text())


# --- derivation -------------------------------------------------------------

def test_voice_file_derives_from_node_and_index():
    assert voice_file("n1", 0) == "vo_n1_0.wav"
    assert voice_file("intro", 3) == "vo_intro_3.wav"


def test_voiced_lines_skips_narration_and_keeps_index():
    nodes = [{"id": "n1", "lines": [
        {"speaker": None, "text": "narration"},      # skipped
        {"speaker": "al", "text": "hi"},             # index 1
        {"speaker": "bo", "text": "yo"},             # index 2
    ]}]
    got = [(nid, i, ln["speaker"]) for nid, i, ln in voiced_lines(nodes)]
    assert got == [("n1", 1, "al"), ("n1", 2, "bo")]


def test_voiced_lines_handles_missing_lines():
    assert list(voiced_lines([{"id": "n1"}])) == []


# --- projection -------------------------------------------------------------

def test_compile_vn_emits_voice_when_voiced():
    out = compile_vn(_ir(), voiced=True)
    assert 'voice "audio/voice/vo_' in out
    # every voice ref precedes a spoken line, never narration
    for nid, i, _ln in voiced_lines(_ir()["nodes"]):
        assert f'voice "audio/voice/{voice_file(nid, i)}"' in out


def test_compile_vn_silent_by_default():
    assert "audio/voice/" not in compile_vn(_ir())


# --- speaker mapping --------------------------------------------------------

def test_pick_voice_deterministic_and_bounded():
    bank = ["af", "am", "bf"]
    assert pick_voice("al", bank) == pick_voice("al", bank)
    assert pick_voice("al", bank) in bank


def test_pick_voice_no_bank_returns_none():
    assert pick_voice("al", []) is None


# --- placeholder backfill ---------------------------------------------------

def test_ensure_voice_placeholders_writes_silent_wavs(tmp_path):
    from renpy.fns import _ensure_voice_placeholders
    ir = {"nodes": [{"id": "n1", "lines": [
        {"speaker": "al", "text": "hi"},
        {"speaker": None, "text": "narration"},
    ]}]}
    _ensure_voice_placeholders(ir, str(tmp_path))
    wav = tmp_path / "audio" / "voice" / "vo_n1_0.wav"
    assert wav.exists()
    assert not (tmp_path / "audio" / "voice" / "vo_n1_1.wav").exists()  # narration unvoiced
    with wave.open(str(wav)) as w:
        assert w.getnchannels() == 1


def test_ensure_voice_placeholders_keeps_real_clip(tmp_path):
    from renpy.fns import _ensure_voice_placeholders
    voice_dir = tmp_path / "audio" / "voice"
    voice_dir.mkdir(parents=True)
    real = voice_dir / "vo_n1_0.wav"
    real.write_bytes(b"REAL_CLIP")
    _ensure_voice_placeholders({"nodes": [{"id": "n1", "lines": [
        {"speaker": "al", "text": "hi"}]}]}, str(tmp_path))
    assert real.read_bytes() == b"REAL_CLIP"  # not overwritten


# --- gating -----------------------------------------------------------------

def test_generate_voices_skips_without_endpoint(tmp_path, monkeypatch):
    import tools.tts_tools as tts
    monkeypatch.setattr(tts, "_tts_settings", lambda: None)
    from renpy.fns import generate_voices
    assert generate_voices(_ir(), tmp_path)["status"] == "skipped"


def test_generate_voices_skips_non_vn(tmp_path, monkeypatch):
    import tools.tts_tools as tts
    import maestro.ir_assemble as ira
    from renpy.fns import generate_voices
    monkeypatch.setattr(tts, "_tts_settings", lambda: {"endpoint": "http://x"})
    monkeypatch.setattr(ira, "assemble_ir", lambda _inputs: {"genre": "point_and_click"})
    res = generate_voices({}, tmp_path)
    assert res["status"] == "skipped"
