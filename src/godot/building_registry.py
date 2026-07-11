"""The 3D building-shell registry — hand-made models keyed by footprint geometry.

src/assets/buildings/registry.json declares every shell: its .glb, the footprint (w, h) it
was built for, and a style tag. At compile, `annotate_shells` resolves each doored footprint
against the registry (exact w x h first, then the transposed footprint — the presenter yaws
meshes anyway), copies the chosen .glb into the project as shell_<file>, and writes the
choice onto the footprint (`fp["shell_file"]`) so the runtime just loads what it's told.
No registry / no match = no annotation; the presenter falls through to the trellis mesh or
the parametric blockout.
"""

import json
import shutil
from pathlib import Path
from typing import Dict, Optional

REGISTRY_DIR = Path(__file__).resolve().parents[1] / "assets" / "buildings"


def _load() -> list:
    path = REGISTRY_DIR / "registry.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("shells", [])


def resolve(w: int, h: int, style: Optional[str] = None) -> Optional[Dict]:
    shells = _load()

    def pick(cands):
        if not cands:
            return None
        if style:
            styled = [s for s in cands if s.get("style") == style]
            if styled:
                return styled[0]
        return cands[0]

    exact = pick([s for s in shells if s["w"] == w and s["h"] == h])
    if exact:
        return exact
    return pick([s for s in shells if s["w"] == h and s["h"] == w])


def annotate_shells(ir: Dict, images_dir: Path, style: Optional[str] = None) -> int:
    annotated = 0
    for place in ir.get("places", []) or []:
        footprints = place.get("footprints")
        if not isinstance(footprints, dict):
            continue
        for fp in footprints.values():
            if fp.get("door") is None:
                continue
            shell = resolve(int(fp.get("w", 1)), int(fp.get("h", 1)), style)
            if shell is None:
                continue
            src = REGISTRY_DIR / shell["file"]
            if not src.exists():
                continue
            dest_name = f"shell_{shell['file']}"
            shutil.copy2(src, images_dir / dest_name)
            fp["shell_file"] = dest_name
            annotated += 1
    return annotated
