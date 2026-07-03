"""compile_ir — the IR compile backend.

Assembles the on-disk components into one Game IR, gates on cross-reference integrity
(ir_crossref), projects to Ren'Py source (ir_vn for visual_novel; ir_pnc for point_and_click),
writes a launchable project, and lints it. Returns the same structured pass/fail contract as the
legacy compile_renpy so the `compiles` check and the final packaging step are unchanged.
"""

import contextlib
import io
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from maestro.ir_crossref import crossref_errors
from maestro.ir_assemble import assemble_ir
from renpy.compiler import compile_gate
from renpy.ir_vn import compile_vn
from renpy.lint import node_line_ranges, run_final_lint, pnc_line_ranges
from renpy.fns import (_get_sdk_path, _merge_cast_into_manifest, _ensure_placeholder_images,
                       _ensure_expression_placeholders, _ensure_voice_placeholders)
from renpy.renpy_builder import _copy_templates, _distribute, write_options_rpy

_REPO = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO / "docs" / "game_ir.schema.json"

_REQUIRED = ("asset_manifest",)
_FAIL = {"lint_error_count": None, "project_dir": None}


def _schema_errors(ir: Dict) -> List[str]:
    import jsonschema
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        {k: v for k, v in schema.items() if k != "examples"})
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in validator.iter_errors(ir)]


def _load(working_dir: Path) -> Dict:
    inputs: Dict = {}
    for stem in ("brief", "characters", "story", "asset_manifest", "items", "nodes", "places",
                 "matches", "spec"):
        path = working_dir / f"{stem}.json"
        if path.exists():
            inputs[stem] = json.loads(path.read_text(encoding="utf-8"))
    return inputs


def compile_ir(working_dir, distribute: bool = True) -> Dict:
    working_dir = Path(working_dir)
    inputs = _load(working_dir)

    missing = [f for f in _REQUIRED if f not in inputs]
    if "nodes" not in inputs and "places" not in inputs:
        missing.append("one of nodes/places")
    if missing:
        return {"ok": False, "reason": f"missing components: {missing}", **_FAIL}

    from maestro.modules import unprojectable
    from renpy.projections import register as register_renpy_projections
    register_renpy_projections()

    spec_data = inputs.get("spec", {}) or {}
    module_ids = spec_data.get("modules", [])
    missing = unprojectable("renpy", module_ids)
    if missing:
        return {"ok": False, "reason":
                f"the renpy engine has no projection for module(s) {missing} — "
                f"this game needs an engine that renders them (e.g. web)", **_FAIL}

    ir = assemble_ir(inputs)

    # Schema first: crossref assumes a well-formed IR, so a structural error must surface here
    # rather than crash the reference walk.
    errs = _schema_errors(ir)
    if errs:
        return {"ok": False, "reason": "invalid IR — " + "; ".join(errs[:5]), **_FAIL}

    # Hard gate: every id reference must resolve. Replaces the legacy _find_script_issues.
    errs = crossref_errors(ir)
    if errs:
        return {"ok": False, "reason": "unresolved references — " + "; ".join(errs[:5]), **_FAIL}

    if ir["genre"] == "rpg":
        from maestro.modules.world import _RPG_KINDS
        place = next(p for p in ir.get("places", []) if p.get("kind") in _RPG_KINDS)
        return {"ok": False, "reason":
                f"the renpy engine cannot play walkable places — place '{place['id']}' has "
                f"kind '{place['kind']}'; this game needs the godot engine", **_FAIL}

    from tools.tts_tools import voice_enabled
    voiced = ir["genre"] == "visual_novel" and voice_enabled()
    if ir["genre"] == "point_and_click":
        from renpy.ir_pnc import compile_pnc
        script = compile_pnc(ir)
    else:
        script = compile_vn(ir, voiced=voiced)

    output_dir = str(working_dir / "game_output")
    game_dir = os.path.join(output_dir, "game")
    os.makedirs(game_dir, exist_ok=True)

    spec = inputs.get("spec", {}) or {}
    title = spec.get("title") or (inputs.get("brief", {}) or {}).get("title") or "Untitled"
    tc = (inputs.get("asset_manifest", {}) or {}).get("title_card", {}) or {}
    menu_bg = tc.get("image_file", "title_card.png") if tc.get("description") else ""
    write_options_rpy(game_dir, title, about=spec.get("concept", ""), menu_bg=menu_bg)
    with open(os.path.join(game_dir, "script.rpy"), "w", encoding="utf-8") as f:
        f.write(script)

    sdk_path = _get_sdk_path()
    _copy_templates(game_dir, sdk_path)

    if ir["genre"] == "point_and_click":
        _write_pnc_placeholders(ir, game_dir)
    else:
        manifest = _merge_cast_into_manifest(inputs.get("characters", {}),
                                             inputs.get("asset_manifest", {}))
        _ensure_placeholder_images(manifest, game_dir)
        _ensure_expression_placeholders(ir, game_dir)
        if voiced:
            _ensure_voice_placeholders(ir, game_dir)

    # Wire the generated title card onto the main menu. The stock `main_menu` screen does
    # `add gui.main_menu_background`, and Ren'Py's screen language CONST-FOLDS that `define`d
    # name — so reassigning gui.main_menu_background at runtime is ignored. Instead we overwrite
    # the asset the const already points at (gui/main_menu.png), after _copy_templates has laid
    # down the stock one. game_menu_background is left stock.
    if menu_bg:
        from utils.image import write_solid_png
        tc_path = Path(game_dir) / "images" / menu_bg
        if not tc_path.exists():
            write_solid_png(tc_path, 1280, 720, (20, 30, 60))
        gui_bg = Path(game_dir) / "gui" / "main_menu.png"
        gui_bg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tc_path, gui_bg)

    build_result: Dict = {"project_dir": os.path.abspath(output_dir)}
    if sdk_path:
        node_ids = (inputs.get("nodes", {}) or {}).get("node_ids", [])
        if ir["genre"] == "point_and_click":
            place_ids = (inputs.get("places", {}) or {}).get("place_ids", [])
            ranges = pnc_line_ranges(script, place_ids, node_ids)
        else:
            ranges = node_line_ranges(script, node_ids)
        with contextlib.redirect_stdout(io.StringIO()) if not distribute else _nullcm():
            build_result["lint"] = run_final_lint(output_dir, sdk_path, node_ranges=ranges)
        if distribute:
            build_result.update(_distribute(output_dir, sdk_path))
    else:
        build_result["lint"] = {"error_count": None}

    reason: Optional[str] = compile_gate(build_result)
    lint_errors = build_result.get("lint", {}).get("errors") or []
    if reason and "lint error" in reason and lint_errors:
        reason = f"{reason}: " + " | ".join(lint_errors[:5])
    return {
        "ok": reason is None,
        "reason": reason,
        "lint_error_count": build_result.get("lint", {}).get("error_count"),
        "lint_errors": lint_errors,
        "dist_returncode": build_result.get("dist_returncode"),
        "dist_error": build_result.get("dist_error"),
        "project_dir": build_result.get("project_dir"),
    }


def _write_pnc_placeholders(ir: Dict, game_dir: str) -> None:
    """Write a solid placeholder for every background/item image ir_pnc references (background
    ids resolve through ir["backgrounds"], falling back to `<id>.png`), so the project lints
    before real art is generated."""
    from utils.image import write_solid_png
    images_dir = Path(game_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    bg_files = {bg["id"]: bg["image_file"] for bg in ir.get("backgrounds", [])}
    for bg in {p.get("background") for p in ir.get("places", []) if p.get("background")}:
        path = images_dir / bg_files.get(bg, f"{bg}.png")
        if not path.exists():
            write_solid_png(path, 1280, 720, (58, 58, 92))
    for it in ir.get("items", []):
        path = images_dir / f"{it['id']}.png"
        if not path.exists():
            write_solid_png(path, 128, 128, (120, 100, 40))


@contextlib.contextmanager
def _nullcm():
    yield
