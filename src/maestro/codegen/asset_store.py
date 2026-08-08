"""The asset STORE: rendered object types, shared across games, so a mesh that cost ~30s of
GPU is never paid for twice.

An entry is a TYPE, not a game's asset: `<data_dir>/asset_store/<key>/` holding every
intermediate the pipeline produced — `subject.png` (the matted Qwen render, the 3D leg's
input), `mesh.glb` (TRELLIS output, the reskin leg's input), `sprite.png` (the shared-camera
orthographic render games composite), and `meta.json`. Dropping an intermediate forces
re-paying the stage upstream of it, so all three are kept.

`meta.json` stamps which model produced each piece, so a model upgrade invalidates exactly
its own leg: a new subject model regenerates subjects and keeps every GLB whose subject
didn't change; a new camera re-renders sprites only.

Games REFERENCE entries, never own them: the scene pipeline copies `sprite.png` into the
game folder at composite time. The store itself is touched only at build time — /play never
reads it — so a miss costs build wall-clock, invisible to players.

Concurrency is claim-then-fill, like the job queue: `claim()` is an O_EXCL create, so two
builds missing the same type enqueue one render. A claim carries its run_id so a dead
build's claim can be reaped by age rather than blocking the type forever.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# One claim outlives any healthy render chain (subject ~20s + TRELLIS ~30s + queue time);
# past this it is a dead build's leftovers and the type is up for grabs again.
CLAIM_TTL_SECONDS = 30 * 60

_KEY = re.compile(r"^[a-z0-9_]{1,80}$")


def store_dir() -> Path:
    from config.settings_manager import settings_manager
    d = Path(settings_manager.get_settings()["data_dir"]) / "asset_store"
    d.mkdir(parents=True, exist_ok=True)
    return d


def type_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:80]
    return key or "thing"


def entry_dir(key: str) -> Path:
    if not _KEY.match(key):
        raise ValueError(f"bad store key: {key!r}")
    return store_dir() / key


def read_meta(key: str) -> Dict:
    p = entry_dir(key) / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("asset_store: %s meta.json is not valid JSON", key)
        return {}


def _write_meta(key: str, meta: Dict) -> None:
    d = entry_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                 encoding="utf-8")


def sprite_path(key: str) -> Path:
    return entry_dir(key) / "sprite.png"


def mesh_path(key: str) -> Path:
    return entry_dir(key) / "mesh.glb"


def subject_path(key: str) -> Path:
    return entry_dir(key) / "subject.png"


def lookup(key: str) -> Optional[Dict]:
    """A COMPLETE entry (sprite on disk + meta recorded), or None. Partial deposits — a
    subject whose TRELLIS leg died — read as misses so the chain re-runs from what exists."""
    if not _KEY.match(key):
        return None
    if not sprite_path(key).exists():
        return None
    meta = read_meta(key)
    if not meta.get("phrase"):
        return None
    return {"key": key, "dir": str(entry_dir(key)), "meta": meta}


def claim(key: str, run_id: str) -> bool:
    """True iff this caller now owns rendering the type. O_EXCL create is the atomicity;
    a stale claim (older than CLAIM_TTL_SECONDS) is replaced rather than honored."""
    d = entry_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    c = d / ".claim"
    try:
        with c.open("x", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": run_id, "at": time.time()}))
        return True
    except FileExistsError:
        try:
            held = json.loads(c.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            held = {}
        if time.time() - float(held.get("at") or 0) > CLAIM_TTL_SECONDS:
            c.write_text(json.dumps({"run_id": run_id, "at": time.time()}), encoding="utf-8")
            return True
        return False


def release(key: str) -> None:
    (entry_dir(key) / ".claim").unlink(missing_ok=True)


def deposit_subject(key: str, data: bytes, phrase: str, model: str) -> None:
    subject_path(key).parent.mkdir(parents=True, exist_ok=True)
    subject_path(key).write_bytes(data)
    meta = read_meta(key)
    meta.update({"phrase": phrase, "subject_model": model, "deposited_at": time.time()})
    _write_meta(key, meta)


def deposit_mesh(key: str, data: bytes, model: str) -> None:
    mesh_path(key).write_bytes(data)
    meta = read_meta(key)
    meta["mesh_model"] = model
    _write_meta(key, meta)


def deposit_sprite(key: str, data: bytes, camera: str) -> None:
    """The sprite completes the entry; the claim comes off with it."""
    sprite_path(key).write_bytes(data)
    meta = read_meta(key)
    meta["sprite_camera"] = camera
    _write_meta(key, meta)
    release(key)


def entries() -> List[Dict]:
    """Every complete entry — what the admin gallery lists, so a human can evict a lemon."""
    out = []
    root = store_dir()
    for d in sorted(root.iterdir()):
        if d.is_dir():
            e = lookup(d.name)
            if e:
                out.append(e)
    return out


def evict(key: str) -> bool:
    """Remove an entry wholesale — the human quality lever. The next map that needs the
    type re-renders it."""
    d = entry_dir(key)
    if not d.exists():
        return False
    for p in d.iterdir():
        p.unlink()
    d.rmdir()
    logger.info("asset_store: evicted %s", key)
    return True


# ---------------------------------------------------------------------------
# Type resolution: a plan's flavor names -> store keys + subject phrases.
#
# The keyword table this replaces failed its first battery (26 of 32 types fell through to
# raw flavor names, and every biome's houses collapsed to one cached cottage). Resolution
# is one small llm call for the WHOLE list; the fallback when it fails is the name itself,
# styled — a miss renders correctly and caches under its own key, it just reuses less.

RESOLVE_PROMPT = (
    "You normalize object names from game map plans into reusable asset types.\n"
    "For each NAME, give:\n"
    "- type: a generic lowercase type key of 1-3 words (e.g. \"cottage\", \"market stall\","
    " \"bell tower\") — strip flavor and proper nouns (\"The Salty Dog Inn\" -> \"inn\")\n"
    "- phrase: a short concrete visual description of the object for an image model,"
    " matching the STYLE\n"
    "Answer ONLY a JSON object mapping each input name to {\"type\": ..., \"phrase\": ...}.\n"
    "\n"
    "STYLE: {style}\n"
    "NAMES:\n{names}\n")


def resolve_types(names: List[str], style: str, run_id: str) -> Dict[str, Dict]:
    """{name: {"key", "phrase"}} for every unique name. The style rides the KEY (a desert
    inn and a snow inn are different store entries) so the store never serves the battery's
    cottage-in-the-desert."""
    uniq = list(dict.fromkeys(n for n in names if n))
    if not uniq:
        return {}
    style_key = type_key(style)[:24] if style else ""
    resolved = _llm_resolve(uniq, style or "hand-painted fantasy game art", run_id)
    out = {}
    for name in uniq:
        r = resolved.get(name) or {}
        base = type_key(r.get("type") or name)
        phrase = (r.get("phrase") or "").strip() or name
        key = f"{base}__{style_key}" if style_key else base
        out[name] = {"key": key[:80], "phrase": phrase}
    return out


def _llm_resolve(names: List[str], style: str, run_id: str) -> Dict[str, Dict]:
    from llm_clients.connector import get_connector
    from llm_clients.message_builder import MessageBuilder
    from tools.execution_context import run_scope
    prompt = (RESOLVE_PROMPT.replace("{style}", style)
              .replace("{names}", "\n".join(f"- {n}" for n in names)))
    try:
        with run_scope(run_id):
            reply = get_connector().generate_with_tools(
                MessageBuilder("You output only JSON.").add_user(prompt).build(), [],
                max_tokens=2000)
        text = ((reply.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
        data = _extract_json(text)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        logger.exception("asset_store: type resolution failed for %d name(s)", len(names))
    return {}


def _extract_json(text: str):
    s = text.find("{")
    if s < 0:
        return None
    depth = 0
    for i in range(s, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[s:i + 1])
                except ValueError:
                    return None
    return None
