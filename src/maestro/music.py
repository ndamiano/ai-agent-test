"""Music-track derivation + the filename/playback contract.

Music is DECLARED as data (asset_manifest.music / ir.music) but for v1 the track set is DERIVED
deterministically — from WHERE the game happens (place kinds for a world/room game, scene locations
for a VN) and HOW it feels (the story spine's tone/theme) — with no LLM call, mirroring how
_merge_cast_into_manifest backfills sprite entries. Both the generation pass (renpy.fns.generate_music)
and the engine playback projections read this same derived set, so the file a track references is the
file generation writes.

Pure + engine-neutral: dicts in, dicts out. IO (synthesis, placeholder backfill, playback) lives in
the engine backends.
"""

import re
from typing import Dict, Optional


def _slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return out or "x"


def music_file(track_id: str) -> str:
    """Filename under game/audio/music/ for a track id. Shared by derivation, generation, and the
    placeholder backfill so the referenced file IS the generated file (voice_file's analogue)."""
    return f"{track_id}.wav"


def _prompt(mood: str, where: str, theme: str) -> str:
    base = f"{mood} instrumental ambient background music for {where}"
    if theme:
        base += f", evoking {theme}"
    return base + ", looping, no vocals"


def derive_music(ir: Dict, tone: str = "", theme: str = "") -> Optional[Dict]:
    """Derive the coarse track set from an assembled IR + the story spine's tone/theme. A world/PnC
    game gets one ambient bed per distinct place KIND (a handful — town/interior/world_map/room),
    keyed by place id; a VN gets one bed per distinct scene LOCATION (nodes cluster by background),
    keyed by background id. Every game also gets a `default` main-theme bed for anything unkeyed.
    Returns None only if the IR carries no nodes/places at all."""
    tone = (tone or "").strip()
    theme = (theme or "").strip()
    mood = tone or theme or "calm"

    if not ir.get("nodes") and not ir.get("places"):
        return None

    tracks: Dict[str, Dict] = {}
    by_place: Dict[str, str] = {}
    by_location: Dict[str, str] = {}

    def _track(tid: str, prompt: str) -> str:
        tracks.setdefault(tid, {"id": tid, "file": music_file(tid), "prompt": prompt})
        return tid

    default = _track("music_main", _prompt(mood, "the game's main theme", theme))

    for p in ir.get("places", []):
        if not isinstance(p, dict) or not p.get("id"):
            continue
        kind = p.get("kind") or "room"
        tid = _track(f"music_{_slug(kind)}", _prompt(mood, f"a {kind.replace('_', ' ')} area", theme))
        by_place[p["id"]] = tid

    located = {n.get("location") for n in ir.get("nodes", [])
               if isinstance(n, dict) and n.get("location")}
    for b in ir.get("backgrounds", []):
        bid = b.get("id") if isinstance(b, dict) else None
        if bid and bid in located:
            tid = _track(f"music_{_slug(bid)}", _prompt(mood, f"the scene '{bid}'", theme))
            by_location[bid] = tid

    out: Dict = {"tracks": list(tracks.values()), "default": default}
    if by_place:
        out["by_place"] = by_place
    if by_location:
        out["by_location"] = by_location
    return out


def track_for_place(music: Optional[Dict], place_id) -> Optional[str]:
    if not music:
        return None
    return (music.get("by_place") or {}).get(place_id) or music.get("default")


def track_for_location(music: Optional[Dict], bg_id) -> Optional[str]:
    if not music:
        return None
    return (music.get("by_location") or {}).get(bg_id) or music.get("default")


def file_for_track(music: Optional[Dict], track_id) -> Optional[str]:
    for t in (music or {}).get("tracks", []):
        if t.get("id") == track_id:
            return t.get("file")
    return None
