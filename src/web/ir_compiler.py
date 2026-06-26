"""compile_ir — the web compile backend.

Assembles the on-disk components into one Game IR (maestro.ir_assemble), gates on
cross-reference integrity (maestro.ir_crossref) and JSON-Schema validity, then writes a
self-contained browser project: game.json (the IR) + the static runtime + placeholder/real
art. Returns the same structured pass/fail contract as renpy's compile_ir.
"""

import json
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from maestro.ir_assemble import assemble_ir
from maestro.ir_crossref import crossref_errors

_REPO = Path(__file__).resolve().parents[2]
_RUNTIME = Path(__file__).resolve().parent / "runtime"
_SCHEMA_PATH = _REPO / "docs" / "game_ir.schema.json"

_REQUIRED = ("premise", "asset_manifest")
_FAIL = {"lint_error_count": None, "project_dir": None}


def _load(working_dir: Path) -> Dict:
    inputs: Dict = {}
    for stem in ("brief", "premise", "asset_manifest", "nodes", "places", "matches", "spec"):
        path = working_dir / f"{stem}.json"
        if path.exists():
            inputs[stem] = json.loads(path.read_text(encoding="utf-8"))
    return inputs


def _schema_errors(ir: Dict) -> List[str]:
    import jsonschema
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        {k: v for k, v in schema.items() if k != "examples"})
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in validator.iter_errors(ir)]


def compile_ir(working_dir, distribute: bool = True) -> Dict:
    working_dir = Path(working_dir)
    inputs = _load(working_dir)

    missing = [f for f in _REQUIRED if f not in inputs]
    if "nodes" not in inputs and "places" not in inputs:
        missing.append("one of nodes/places")
    if missing:
        return {"ok": False, "reason": f"missing components: {missing}", **_FAIL}

    from maestro.modules import unprojectable
    from web.projections import register as register_web_projections
    register_web_projections()

    spec_data = inputs.get("spec", {}) or {}
    module_ids = spec_data.get("modules", [])
    miss = unprojectable("web", module_ids)
    if miss:
        return {"ok": False, "reason":
                f"the web engine has no projection for module(s) {miss}", **_FAIL}

    ir = assemble_ir(inputs)

    # Schema first: crossref assumes a well-formed IR, so a structural error must surface here
    # rather than crash the reference walk.
    errs = _schema_errors(ir) or crossref_errors(ir)
    output_dir = working_dir / "game_output"
    if not errs:
        write_web_project(ir, output_dir, src_images=working_dir / "game_output" / "game" / "images")

    dist_path = None
    if not errs and distribute:
        dist_path = _zip(output_dir)

    reason = ("invalid IR — " + "; ".join(errs[:5])) if errs else None
    return {
        "ok": reason is None,
        "reason": reason,
        "lint_error_count": len(errs) if errs else 0,
        "lint_errors": errs,
        "ir_errors": errs,
        "project_dir": str(output_dir.resolve()),
        "dist_path": dist_path,
    }


def write_web_project(ir: Dict, output_dir, src_images=None) -> None:
    """Write game.json + the static runtime + art into output_dir. Pure projection of a whole
    IR dict — usable directly (e.g. to play a docs/examples IR) without on-disk components."""
    output_dir = Path(output_dir)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "game.json").write_text(json.dumps(ir, ensure_ascii=False, indent=2),
                                          encoding="utf-8")

    for f in _RUNTIME.iterdir():
        if f.is_file():
            shutil.copy2(f, output_dir / f.name)

    _write_placeholders(ir, images_dir)
    if src_images is not None and Path(src_images).is_dir():
        for img in Path(src_images).iterdir():
            if img.is_file():
                shutil.copy2(img, images_dir / img.name)


def _write_placeholders(ir: Dict, images_dir: Path) -> None:
    """A solid PNG for every image the runtime may request, so a build with no real art still
    renders. The runtime also colour-fills on load error, so this is belt-and-suspenders."""
    from utils.image import write_solid_png

    def put(name: str, w: int, h: int, color):
        if name and not (images_dir / name).exists():
            write_solid_png(images_dir / name, w, h, color)

    bg_files = {bg["id"]: bg["image_file"] for bg in ir.get("backgrounds", [])}
    for f in bg_files.values():
        put(f, 1280, 720, (58, 58, 92))
    # Background ids referenced without a manifest mapping (e.g. raw example IR) → <id>.png.
    referenced_bg = {n.get("location") for n in ir.get("nodes", []) if n.get("location")}
    referenced_bg |= {p.get("background") for p in ir.get("places", []) if p.get("background")}
    for bid in referenced_bg:
        if bid not in bg_files:
            put(f"{bid}.png", 1280, 720, (58, 58, 92))
    for c in ir.get("characters", []):
        if c.get("sprite"):
            put(c["sprite"], 512, 768, (92, 58, 92))
    for it in ir.get("items", []):
        put(f"{it['id']}.png", 128, 128, (120, 100, 40))


def _zip(output_dir: Path) -> Optional[str]:
    dist = output_dir.parent / "web_build.zip"
    with zipfile.ZipFile(dist, "w", zipfile.ZIP_DEFLATED) as z:
        for f in output_dir.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(output_dir))
    return str(dist.resolve())
