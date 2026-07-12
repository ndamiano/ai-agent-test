"""Music leg (M1): derivation, IR lift, per-engine playback, generation + fail-soft."""
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonschema
import pytest

from maestro.ir_assemble import assemble_ir
from maestro.music import (derive_music, music_file, track_for_place, track_for_location,
                           file_for_track)
from renpy.ir_vn import compile_vn
from renpy.ir_pnc import compile_pnc

_SCHEMA = json.loads((Path(__file__).parent.parent / "docs" / "game_ir.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator({k: v for k, v in _SCHEMA.items() if k != "examples"})


def _vn_artifact():
    return {
        "characters": {"characters": [{"id": "al", "name": "Al"}]},
        "asset_manifest": {"backgrounds": [{"id": "bg_cell", "image_file": "cell.png"},
                                           {"id": "bg_hall", "image_file": "hall.png"}],
                           "characters": []},
        "story": {"spine": {"theme": "loss", "tone": "somber"}},
        "nodes": {
            "node_ids": ["n1", "n2"],
            "nodes": {
                "n1": {"location": "bg_cell", "lines": [{"speaker": "al", "text": "hi"}],
                       "end": {"type": "jump", "target": "n2"}},
                "n2": {"location": "bg_hall", "lines": [{"speaker": "al", "text": "bye"}],
                       "end": {"type": "end", "ending": "done"}},
            },
            "start": "n1",
        },
    }


def _pnc_artifact():
    return {
        "characters": {"characters": []},
        "asset_manifest": {"backgrounds": [{"id": "bg_room", "image_file": "room.png"}],
                           "characters": []},
        "story": {"spine": {"theme": "mystery", "tone": "tense"}},
        "nodes": {"node_ids": [], "nodes": {}},
        "places": {
            "place_ids": ["room1"],
            "places": {"room1": {"kind": "room", "background": "bg_room", "interactables": [
                {"id": "h1", "label": "door", "position": {"rect": {"x": 1, "y": 1, "w": 9, "h": 9}},
                 "action": {"type": "win"}}]}},
            "start_place": "room1",
        },
    }


# ── derivation ────────────────────────────────────────────────────────────────
def test_music_file_contract():
    assert music_file("music_main") == "music_main.wav"


def test_derive_vn_one_track_per_used_location_plus_default():
    ir = assemble_ir(_vn_artifact())
    m = ir["music"]
    assert m["default"] == "music_main"
    ids = {t["id"] for t in m["tracks"]}
    assert ids == {"music_main", "music_bg_cell", "music_bg_hall"}
    assert m["by_location"] == {"bg_cell": "music_bg_cell", "bg_hall": "music_bg_hall"}
    assert "by_place" not in m
    # the somber tone reaches the generation prompt
    assert any("somber" in t["prompt"] for t in m["tracks"])


def test_derive_ignores_unused_background():
    art = _vn_artifact()
    art["asset_manifest"]["backgrounds"].append({"id": "bg_unused", "image_file": "u.png"})
    m = assemble_ir(art)["music"]
    assert "bg_unused" not in m["by_location"]  # no node uses it → no track


def test_derive_world_one_bed_per_place_kind():
    art = _pnc_artifact()
    art["places"]["places"]["room2"] = {"kind": "room", "background": "bg_room",
                                        "interactables": []}
    art["places"]["place_ids"].append("room2")
    m = assemble_ir(art)["music"]
    # two rooms share one kind-keyed bed
    assert m["by_place"] == {"room1": "music_room", "room2": "music_room"}
    assert {t["id"] for t in m["tracks"]} == {"music_main", "music_room"}


def test_resolvers_fall_back_to_default():
    m = {"tracks": [{"id": "music_main", "file": "music_main.wav"}], "default": "music_main",
         "by_place": {"town": "music_town"}, "by_location": {"bg_x": "music_bg_x"}}
    assert track_for_place(m, "town") == "music_town"
    assert track_for_place(m, "unknown") == "music_main"
    assert track_for_location(m, "unknown") == "music_main"
    assert track_for_place(None, "x") is None
    assert file_for_track(m, "music_main") == "music_main.wav"
    assert file_for_track(m, "missing") is None


# ── IR lift ───────────────────────────────────────────────────────────────────
def test_assembled_ir_with_music_is_schema_valid():
    _VALIDATOR.validate(assemble_ir(_vn_artifact()))
    _VALIDATOR.validate(assemble_ir(_pnc_artifact()))


# ── Ren'Py playback ─────────────────────────────────────────────────────────────
def test_compile_vn_plays_the_right_bed_per_scene():
    ir = assemble_ir(_vn_artifact())
    out = compile_vn(ir)
    # each located node's label carries its location's bed, gated if_changed
    assert 'play music "audio/music/music_bg_cell.wav"' in out
    assert 'play music "audio/music/music_bg_hall.wav"' in out
    assert "if_changed" in out


def test_compile_vn_no_music_when_ir_has_none():
    ir = assemble_ir(_vn_artifact())
    ir.pop("music")
    assert "audio/music/" not in compile_vn(ir)


def test_compile_pnc_plays_bed_on_place_entry():
    out = compile_pnc(assemble_ir(_pnc_artifact()))
    assert 'play music "audio/music/music_room.wav"' in out


# ── generation + fail-soft ──────────────────────────────────────────────────────
def _valid_wav(path: Path) -> int:
    with wave.open(str(path)) as w:
        return w.getnframes()


def test_generate_music_skips_without_backend(tmp_path, monkeypatch):
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_music_settings", lambda: {})
    res = fns.generate_music(_vn_artifact(), tmp_path)
    assert res["status"] == "skipped" and "backend" in res["reason"]
    assert not (tmp_path / "game_output" / "game" / "audio" / "music").exists()


def test_generate_music_degrades_to_silence_on_backend_failure(tmp_path, monkeypatch):
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_music_settings", lambda: {"backend": "musicgen", "endpoint": ""})
    res = fns.generate_music(_vn_artifact(), tmp_path)
    assert res["status"] == "ok" and res["failed"]          # every track fell back
    d = tmp_path / "game_output" / "game" / "audio" / "music"
    silent = d / "music_main.wav"
    assert silent.exists()                                   # placeholder still there
    assert not silent.read_bytes()[44:].strip(b"\x00")       # and it is silent


def test_generate_music_skips_when_no_tracks(tmp_path):
    from renpy.fns import generate_music
    assert generate_music({"nodes": {"node_ids": [], "nodes": {}}}, tmp_path)["status"] == "skipped"


def test_ensure_music_placeholders_backfills_missing_keeps_real(tmp_path):
    from renpy.fns import _ensure_music_placeholders
    ir = {"music": {"tracks": [{"id": "music_main", "file": "music_main.wav"},
                               {"id": "music_town", "file": "music_town.wav"}]}}
    real = tmp_path / "audio" / "music" / "music_town.wav"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"REAL")
    _ensure_music_placeholders(ir, str(tmp_path))
    assert (tmp_path / "audio" / "music" / "music_main.wav").exists()   # backfilled
    assert real.read_bytes() == b"REAL"                                 # not clobbered


# ── Godot packaging ─────────────────────────────────────────────────────────────
def test_godot_project_ships_music_placeholders_kept(tmp_path):
    from godot.ir_compiler import write_godot_project
    ir = assemble_ir(_pnc_artifact())
    out = tmp_path / "godot_output"
    write_godot_project(ir, out)
    for t in ir["music"]["tracks"]:
        wav = out / "audio" / "music" / t["file"]
        assert wav.exists()                                    # placeholder shipped
        assert (out / "audio" / "music" / (t["file"] + ".import")).exists()  # importer=keep
