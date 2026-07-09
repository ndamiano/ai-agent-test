"""compile_ir — the Godot compile backend.

Assembles the on-disk components into one Game IR (maestro.ir_assemble), gates on cross-reference
integrity (maestro.ir_crossref) and JSON-Schema validity, then writes a self-contained Godot 4
project: game.json (the IR) + the static GDScript runtime + placeholder/real art. Returns the
loop's structured pass/fail contract.

Optionally exports a native binary if a `godot` binary + export templates are present; that step is
best-effort and never fails the build (the runnable project dir is the deliverable).
"""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from maestro.ir_assemble import assemble_ir
from maestro.ir_crossref import crossref_errors

_REPO = Path(__file__).resolve().parents[2]
_RUNTIME = Path(__file__).resolve().parent / "runtime"
_SCHEMA_PATH = _REPO / "docs" / "game_ir.schema.json"

_REQUIRED = ("asset_manifest",)
_FAIL = {"lint_error_count": None, "project_dir": None}


def _load(working_dir: Path) -> Dict:
    inputs: Dict = {}
    for stem in ("brief", "characters", "story", "asset_manifest", "items", "nodes", "places",
                 "combat", "spec"):
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
    from godot.projections import register as register_godot_projections
    register_godot_projections()

    spec_data = inputs.get("spec", {}) or {}
    module_ids = spec_data.get("modules", [])
    miss = unprojectable("godot", module_ids)
    if miss:
        return {"ok": False, "reason":
                f"the godot engine has no projection for module(s) {miss}", **_FAIL}

    ir = assemble_ir(inputs)

    # Schema first: crossref assumes a well-formed IR, so a structural error must surface here
    # rather than crash the reference walk.
    errs = _schema_errors(ir) or crossref_errors(ir)
    output_dir = working_dir / "godot_output"
    if not errs:
        write_godot_project(ir, output_dir,
                            src_images=working_dir / "game_output" / "game" / "images")

    dist_path = None
    if not errs and distribute:
        # Prefer a self-contained export (engine embedded — the player needs no Godot install).
        # Fall back to the raw project zip only when no godot binary/templates are present.
        dist_path = _export_dist(output_dir) or _zip(output_dir)

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


def write_godot_project(ir: Dict, output_dir, src_images=None) -> None:
    """Write game.json + the static GDScript runtime + art into output_dir. Pure projection of a
    whole IR dict — usable directly (e.g. to play a docs/examples combat IR) without on-disk
    components."""
    output_dir = Path(output_dir)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "game.json").write_text(json.dumps(ir, ensure_ascii=False, indent=2),
                                          encoding="utf-8")

    _copy_runtime(_RUNTIME, output_dir)

    _write_placeholders(ir, images_dir)
    if src_images is not None and Path(src_images).is_dir():
        for img in Path(src_images).iterdir():
            if img.is_file():
                shutil.copy2(img, images_dir / img.name)

    # Audio (voice + music): the runtime loads it as raw bytes too, so it ships beside the images.
    # Copy the generated tree, then backfill a silent placeholder for any referenced-but-missing
    # music track (belt-and-suspenders — the runtime also degrades to no-music on a load miss).
    audio_dir = output_dir / "audio"
    if src_images is not None and (Path(src_images).parent / "audio").is_dir():
        shutil.copytree(Path(src_images).parent / "audio", audio_dir, dirs_exist_ok=True)
    _write_music_placeholders(ir, audio_dir)

    _keep_imports(images_dir)
    music_dir = audio_dir / "music"
    if music_dir.is_dir():
        _keep_imports(music_dir)
    voice_dir = audio_dir / "voice"
    if voice_dir.is_dir():
        _keep_imports(voice_dir)


def _write_music_placeholders(ir: Dict, audio_dir: Path) -> None:
    from utils.audio import write_silent_wav
    music_dir = audio_dir / "music"
    for t in (ir.get("music") or {}).get("tracks", []):
        path = music_dir / t["file"]
        if not path.exists():
            write_silent_wav(path, seconds=1.0)


def _keep_imports(files_dir: Path) -> None:
    """The runtime reads every asset as RAW BYTES (FileAccess.get_file_as_bytes), never via the
    resource system. Godot's default import converts images/meshes/audio to engine resources and
    STRIPS the source file from the export, so FileAccess reads nothing in the exported .pck.
    importer=keep ships each file untouched — the raw bytes survive in the pack. (It also skips the
    expensive per-file import conversion.)"""
    for f in files_dir.iterdir():
        if f.is_file() and f.suffix != ".import":
            f.with_name(f.name + ".import").write_text(
                '[remap]\n\nimporter="keep"\n', encoding="utf-8")


def _copy_runtime(runtime: Path, output_dir: Path) -> None:
    """Copy the Godot project (files + the addons/ tree) into output_dir, leaving game.json and
    images/ (written by the caller) in place."""
    for src in runtime.rglob("*"):
        if src.is_file():
            dest = output_dir / src.relative_to(runtime)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


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
    referenced_bg = {n.get("location") for n in ir.get("nodes", []) if n.get("location")}
    referenced_bg |= {p.get("background") for p in ir.get("places", []) if p.get("background")}
    referenced_bg |= {e.get("background") for e in ir.get("encounters", []) if e.get("background")}
    for bid in referenced_bg:
        if bid not in bg_files:
            put(f"{bid}.png", 1280, 720, (58, 58, 92))
    for c in ir.get("characters", []):
        if c.get("sprite"):
            put(c["sprite"], 512, 768, (92, 58, 92))
    for it in ir.get("items", []):
        put(f"{it['id']}.png", 128, 128, (120, 100, 40))


# Per-preset output path (relative to the project). Web emits a folder of files (index.html + wasm
# + pck) you host or zip; the desktop presets emit one self-contained binary.
_EXPORT_OUT = {
    "Web": ("web", "index.html"),
    "Linux": ("export", "game.x86_64"),
    "Windows": ("export", "game.exe"),
    "macOS": ("export", "game.zip"),
}


def export_build(project_dir, preset: str = "Web") -> Optional[str]:
    """Best-effort export to a distributable. Needs a `godot` binary + the matching export TEMPLATES
    installed; on any absence or failure returns None (the project dir remains the deliverable).
    Returns the directory containing the artifact. Never raises."""
    godot = shutil.which("godot") or shutil.which("godot4")
    if not godot:
        return None
    project_dir = Path(project_dir)
    subdir, fname = _EXPORT_OUT.get(preset, ("export", "game"))
    out_path = project_dir / subdir / fname
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Import first so the resource cache exists, then export against the named preset.
        subprocess.run([godot, "--headless", "--path", str(project_dir), "--import"],
                       capture_output=True, text=True, timeout=300)
        proc = subprocess.run(
            [godot, "--headless", "--path", str(project_dir), "--export-release", preset, str(out_path)],
            capture_output=True, text=True, timeout=600)
    except (subprocess.SubprocessError, OSError):
        return None
    return str(out_path.parent) if proc.returncode == 0 and out_path.exists() else None


def _export_dist(project_dir) -> Optional[str]:
    """Export self-contained desktop binaries (Linux + Windows) and zip them into one archive —
    the player needs no Godot install. Returns None when no godot binary/templates are present,
    so the caller falls back to the raw project zip."""
    project_dir = Path(project_dir)
    exported = [p for p in ("Linux", "Windows") if export_build(project_dir, p)]
    if not exported:
        return None
    export_dir = project_dir / "export"
    if not export_dir.exists():
        return None
    dist = project_dir.parent / "godot_dist.zip"
    with zipfile.ZipFile(dist, "w", zipfile.ZIP_DEFLATED) as z:
        for f in export_dir.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(export_dir))
    return str(dist.resolve())


def _zip(output_dir: Path) -> Optional[str]:
    dist = output_dir.parent / "godot_build.zip"
    with zipfile.ZipFile(dist, "w", zipfile.ZIP_DEFLATED) as z:
        for f in output_dir.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(output_dir))
    return str(dist.resolve())
