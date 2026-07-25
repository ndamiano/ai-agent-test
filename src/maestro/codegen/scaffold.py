"""Control scaffold — the PIPELINE wires the controls; the model authors gameplay behind hooks.

Controls are the one part of a game with exactly ONE correct realization given the frozen spec's
control scheme, so model-authored glue there is pure downside — the same reason worldgen seeds
world.ts.

`seed_scaffold` writes a GENERATED `game/main.ts` from a per-scheme template
(`scaffold_templates/<scheme>.ts.tmpl` — real TypeScript we own, hill-climbable). The scaffold owns
config (deterministic from the scheme: 2D size defaults, 3D `controls:` name), the scheme's
movement (run BEFORE the hook update, so gameplay may adjust/clamp after), and the frame loop; it
delegates everything else to the hook module the model authors — `game.ts` exporting
createState/init/update/draw (2D only)/hud. init asserts `state.player` exists so a hook that
forgets the contract fails loudly at the headless gate.

EVERY game is scaffolded, world-flagged ones included: worldgen seeds the PLACE (world.ts) and the
scaffold seeds the CONTROLS (main.ts) — orthogonal axes, so a village RPG gets the same wired
movement, ground clamp and solid pass as everything else and the model only ever authors game.ts.
"""

import json
import logging
import re
from pathlib import Path

from maestro.codegen.gates import entry_src_path, game_dir, game_files
from maestro.templating import render_template

logger = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).resolve().parent / "scaffold_templates"
ENTRY_HOOK = "game.ts"   # the model-authored entry hook module behind the scaffold

# A world game is outdoors under a sky; config.background also tints the 3D distance fog, so the
# indoor-dark default would fog a village grey.
_SKY = "#a9c7e0"
_DARK_3D = "#101018"


def scheme_of(spec: dict) -> str:
    design = spec.get("design") or {}
    return ((design.get("control") or {}).get("scheme") or "").lower().strip()


def _is_3d(spec: dict) -> bool:
    """`mode` is the authority on 3D-ness, not the scheme name: it routes the renderer and the kit
    doc, so a spec whose scheme drifted from its mode must still get a 3D scaffold (no draw hook)."""
    return spec.get("mode") == "3d" or scheme_of(spec).endswith("-3d")


def _wants_interact(spec: dict) -> bool:
    # Conservative v1 trigger: only the spec's `uses` naming dialogue. Entity-desc sniffing invites
    # false positives, and widening later is a one-line change here.
    uses = (spec.get("design") or {}).get("uses") or []
    return any("dialogue" in str(u).lower() for u in uses)


# Tokenized the same way the spec's control vocabulary is normalized, so the key the scaffold
# registers is the key the spec names.
_KEY_ALIAS = {"space": " ", "spacebar": " ", "esc": "Escape", "escape": "Escape",
              "enter": "Enter", "return": "Enter", "tab": "Tab", "shift": "Shift",
              "ctrl": "Control", "control": "Control",
              "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
              "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
              "arrowleft": "ArrowLeft", "arrowright": "ArrowRight"}
_MOVEMENT_TOKENS = {"w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright",
                    "wasd", "arrows", "arrowkeys", "arrow"}
_MOUSE_TOKEN = re.compile(r"mouse|click|pointer|cursor|drag|wheel|scroll|lmb|rmb|mmb")
_INTERACT_WHAT = re.compile(r"interact|talk|dialog|speak", re.I)


def _interact_keys(spec: dict) -> list:
    """The keys the scaffold's interact action binds: the spec's own interact-shaped control when it
    names one, else E. A hardcoded E would silently unbind a spec that put interact elsewhere — the
    scaffold's later register replaces the hook's by name."""
    controls = (spec.get("design") or {}).get("controls") or {}
    for raw, what in controls.items():
        if not _INTERACT_WHAT.search(str(what)):
            continue
        keys = []
        for tok in re.split(r"[\s/+,|]+", str(raw)):
            if not tok:
                continue
            lc = tok.lower()
            if lc in _MOVEMENT_TOKENS or _MOUSE_TOKEN.search(lc):
                continue
            keys.append(_KEY_ALIAS.get(lc, lc if len(tok) == 1 else tok))
        if keys:
            return keys
    return ["e"]


_HOOK_EXPORT = r"export\s+(?:async\s+)?(?:function\s+{name}\b|const\s+{name}\b)|export\s*\{{[^}}]*\b{name}\b[^}}]*\}}"


def reexport_hooks(run_dir) -> dict:
    """Deterministic bridge for hooks the model authored in the WRONG file: a scaffold hook missing
    from game.ts but exported by exactly one sibling gets a one-line re-export appended to game.ts.
    The model's placement is a reasonable decision (createState beside the world builder); only the
    scaffold's import path is law — bridge them rather than make the model move code.
    Returns {changes, count}."""
    files = game_files(run_dir)
    hook_src = files.get(ENTRY_HOOK)
    if hook_src is None:
        return {"changes": [], "count": 0}
    changes = []
    for hook in ("createState", "init", "update", "draw", "hud"):
        rx = re.compile(_HOOK_EXPORT.format(name=hook))
        if rx.search(hook_src):
            continue
        owners = [n for n, src in files.items()
                  if n != ENTRY_HOOK and not src.lstrip().startswith("// GENERATED")
                  and rx.search(src)]
        if len(owners) == 1:
            hook_src = hook_src.rstrip("\n") + f'\nexport {{ {hook} }} from "./{owners[0]}";\n'
            changes.append(("reexport", f"{hook}<-{owners[0]}"))
    if changes:
        (game_dir(run_dir) / ENTRY_HOOK).write_text(hook_src, encoding="utf-8")
    return {"changes": changes, "count": len(changes)}


# The assertion line the author prompt requires at the END of game.ts. It makes every hook-contract
# violation (wrong param order, wrong return type, missing hook) a tsc error INSIDE game.ts at this
# line, naming the exact member and expected signature — the local, precise error shape the model
# fixes reliably. The pipeline never edits signatures; it only demands this line exist (a prompting
# fix, not a deterministic healer).
CONTRACT_ASSERT_2D = ("const _scaffoldContract: GameHooks<GameState> = "
                      "{ createState, init, update, draw, hud };")
CONTRACT_ASSERT_3D = ("const _scaffoldContract: GameHooks<GameState> = "
                      "{ createState, init, update, hud };")


def contract_assert_line(spec: dict) -> str:
    return CONTRACT_ASSERT_3D if _is_3d(spec) else CONTRACT_ASSERT_2D


def has_contract_assert(run_dir) -> bool:
    p = game_dir(run_dir) / ENTRY_HOOK
    return p.exists() and "_scaffoldContract" in p.read_text(encoding="utf-8")


_STATE_REF_RE = re.compile(r"\bstate\.([A-Za-z_$][\w$]*)")


def _scaffold_source(spec: dict) -> str:
    """The template text this spec will actually be seeded from, partials included."""
    scheme = scheme_of(spec)
    tmpl = _TEMPLATES / f"{scheme}.ts.tmpl"
    if not tmpl.exists():
        tmpl = _TEMPLATES / ("orbital-3d.ts.tmpl" if _is_3d(spec) else "default.ts.tmpl")
    src = tmpl.read_text(encoding="utf-8")
    if _wants_interact(spec):
        mode = "3d" if _is_3d(spec) else "2d"
        for part in (f"interact_{mode}", f"interact_init_{mode}"):
            src += (_TEMPLATES / f"{part}.ts.tmpl").read_text(encoding="utf-8")
    return src


def required_state_fields(spec: dict) -> list:
    """The state fields THIS spec's scaffold touches, read off the template it will actually seed.

    The scaffold steers the game through these and asserts `state.player` at init. Derived from the
    template so it cannot drift from the scaffold it describes.
    """
    return sorted(set(_STATE_REF_RE.findall(_scaffold_source(spec))))


_NONCODE_RE = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'"
                         r"|`(?:[^`\\]|\\.)*`", re.S)
_ASSERT_RE = re.compile(r"if\s*\(\s*!\s*state\.([A-Za-z_$][\w$]*)\s*\)\s*\{?\s*throw\b")
_DEREF_RE = re.compile(r"\bstate\.([A-Za-z_$][\w$]*)\s*[.(\[]")
_MAYBE_RE = re.compile(r"\bstate\.([A-Za-z_$][\w$]*)\s*\?")
_COND_RE = re.compile(r"\b(?:if|while)\s*\(")


def _tested_fields(src: str) -> set:
    """Fields named inside an `if`/`while` test — being checked FOR, not relied on."""
    out = set()
    for m in _COND_RE.finditer(src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
        out.update(_STATE_REF_RE.findall(src[m.end():i]))
    return out


def unguarded_state_fields(run_dir) -> list:
    """The fields the SEEDED main.ts cannot run without: the ones it asserts, and the ones it
    dereferences without a `??`, `?.` or `if` guard anywhere in the file.

    Read off the seeded file rather than the template, so it describes the scaffold this run is
    actually checked against. `state.world ?? []` and `if (state.ground)` are optional BY
    CONSTRUCTION — demanding those of every game would be the pipeline inventing a rule instead of
    stating the kit's.
    """
    p = entry_src_path(run_dir)
    if not p.exists():
        return []
    src = _NONCODE_RE.sub(" ", p.read_text(encoding="utf-8"))
    guarded = set(_MAYBE_RE.findall(src)) | _tested_fields(src)
    return sorted(set(_ASSERT_RE.findall(src)) | (set(_DEREF_RE.findall(src)) - guarded))


def state_contract_notes(spec: dict) -> list:
    """The template's OWN comment lines documenting what those fields must be.

    A name alone does not give the field's shape. The templates document each one beside the code
    that uses it, so the prompt quotes them rather than restating a contract that could drift.
    """
    # Whole comment BLOCKS, not the lines that mention `state.` — these notes wrap, and a
    # continuation line carrying the type often names no field.
    notes, block, hit = [], [], False
    for line in _scaffold_source(spec).splitlines() + [""]:
        stripped = line.strip()
        if stripped.startswith("//"):
            block.append(stripped.lstrip("/ ").rstrip())
            hit = hit or bool(_STATE_REF_RE.search(stripped))
            continue
        if hit:
            notes.extend(block)
        block, hit = [], False
    return notes


def seed_scaffold(state, spec: dict) -> None:
    """Write the GENERATED control-scaffold main.ts for this spec's scheme. The caller seeds only
    when main.ts is absent, so a rebuild/fix never regenerates controls under a half-built game."""
    main = entry_src_path(state.run_dir)
    scheme = scheme_of(spec)
    tmpl = _TEMPLATES / f"{scheme}.ts.tmpl"
    if not tmpl.exists():
        # The default template is 2D — falling to it on a 3D spec would render the game as a flat
        # canvas. orbital is the neutral third-person 3D camera to land on instead.
        tmpl = _TEMPLATES / ("orbital-3d.ts.tmpl" if _is_3d(spec) else "default.ts.tmpl")
    world_import = world_init = ""
    if spec.get("world"):
        # world.ts is seeded before this file, so the scaffold can import it directly — the terrain
        # and building passes stop depending on the model opting in.
        world_import = (_TEMPLATES / "world_import.ts.tmpl").read_text(encoding="utf-8").rstrip("\n")
        world_init = (_TEMPLATES / "world_init.ts.tmpl").read_text(encoding="utf-8").rstrip("\n")
    interact = interact_init = ""
    if _wants_interact(spec):
        # Two halves of one feature: the update-side talk loop (advance/choose/close) and the
        # init-side kit.register("interact", ...) that OPENS it — registered so the binding is
        # machine-readable (bindings/remap) while talkStep keeps the per-frame key reads.
        # The keys are substituted here, not by render_template: the partial rides in as a VALUE,
        # and values are never re-scanned for {tokens}.
        mode = "3d" if _is_3d(spec) else "2d"
        keys = json.dumps(_interact_keys(spec))
        interact = (_TEMPLATES / f"interact_{mode}.ts.tmpl").read_text(encoding="utf-8").rstrip("\n")
        interact_init = (_TEMPLATES / f"interact_init_{mode}.ts.tmpl").read_text(
            encoding="utf-8").rstrip("\n").replace("{interact_keys}", keys)
    game_dir(state.run_dir).mkdir(parents=True, exist_ok=True)
    main.write_text(render_template(tmpl, {"interact": interact, "interact_init": interact_init,
                                           "world_import": world_import, "world_init": world_init,
                                           "background": _SKY if spec.get("world") else _DARK_3D}),
                    encoding="utf-8")
    logger.info("control scaffold seeded (%s): %s", scheme or "no scheme", main)
