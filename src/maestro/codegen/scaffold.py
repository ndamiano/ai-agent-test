"""Control scaffold — the PIPELINE wires the controls; the model authors gameplay behind hooks.

WHY this stage exists: two live builds shipped games where opening them did nothing. One never read
a movement key at all — the probe's dead_controls passed because a space-attack mutated state. One
wired kit.moveTopDown correctly and then a hand-rolled collision loop undid the movement every
frame. Both share a root: controls are the one part of a game with exactly ONE correct realization
given the frozen spec's control scheme, so model-authored glue there is pure downside. This
generalizes the worldgen precedent (pipeline-seeded world.ts) to the control layer.

`seed_scaffold` writes a GENERATED `game/main.ts` from a per-scheme template
(`scaffold_templates/<scheme>.ts.tmpl` — real TypeScript we own, hill-climbable). The scaffold owns
config (deterministic from the scheme: 2D size defaults, 3D `controls:` name), the scheme's
movement (run BEFORE the hook update, so gameplay may adjust/clamp after), and the frame loop; it
delegates everything else to the hook module the model authors — `game.ts` exporting
createState/init/update/draw (2D only)/hud. init asserts `state.player` exists so a hook that
forgets the contract fails loudly at the headless gate.

v1 scope: world-flagged specs keep the worldgen flow untouched (it already prescribes the drive
wiring and is validated end-to-end) — seed_scaffold skips them.
"""

import logging
import re
from pathlib import Path

from maestro.codegen.gates import entry_src_path, game_dir
from maestro.templating import render_template

logger = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).resolve().parent / "scaffold_templates"
_MARK = "// GENERATED control scaffold"
ENTRY_HOOK = "game.ts"   # the model-authored entry hook module behind the scaffold


def scheme_of(spec: dict) -> str:
    design = spec.get("design") or {}
    return ((design.get("control") or {}).get("scheme") or "").lower().strip()


def is_scaffolded(run_dir) -> bool:
    p = entry_src_path(run_dir)
    return p.exists() and p.read_text(encoding="utf-8").lstrip().startswith(_MARK)


def _wants_interact(spec: dict) -> bool:
    # Conservative v1 trigger: only the spec's `uses` naming dialogue. Entity-desc sniffing invites
    # false positives, and widening later is a one-line change here.
    uses = (spec.get("design") or {}).get("uses") or []
    return any("dialogue" in str(u).lower() for u in uses)


_HOOK_EXPORT = r"export\s+(?:async\s+)?(?:function\s+{name}\b|const\s+{name}\b)|export\s*\{{[^}}]*\b{name}\b[^}}]*\}}"


def reexport_hooks(run_dir) -> dict:
    """Deterministic bridge for hooks the model authored in the WRONG file: a scaffold hook missing
    from game.ts but exported by exactly one sibling gets a one-line re-export appended to game.ts.
    The model's placement was a reasonable decision (createState beside the world builder); the
    scaffold's import path is the only thing that's law — bridge them instead of making the model
    move code (measured: a capped run diagnosed this exact split correctly and still couldn't land
    the move by hand). Returns {changes, count}."""
    import re
    from maestro.codegen.gates import game_files
    if not is_scaffolded(run_dir):
        return {"changes": [], "count": 0}
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
    return CONTRACT_ASSERT_3D if scheme_of(spec).endswith("-3d") else CONTRACT_ASSERT_2D


def has_contract_assert(run_dir) -> bool:
    p = game_dir(run_dir) / ENTRY_HOOK
    return p.exists() and "_scaffoldContract" in p.read_text(encoding="utf-8")


def seed_scaffold(state, spec: dict) -> None:
    """Seed the GENERATED control-scaffold main.ts for a non-world game. Idempotent like the
    worldgen seed: any existing main.ts (the scaffold on re-entry, or a pre-scaffold run's authored
    entry) is left alone, so a rebuild/fix never regenerates controls under a half-built game."""
    if spec.get("world"):
        return
    main = entry_src_path(state.run_dir)
    if main.exists():
        return
    scheme = scheme_of(spec)
    tmpl = _TEMPLATES / f"{scheme}.ts.tmpl"
    if not tmpl.exists():
        tmpl = _TEMPLATES / "default.ts.tmpl"
    interact = interact_init = ""
    if _wants_interact(spec):
        # Two halves of one feature: the update-side talk loop (advance/choose/close) and the
        # init-side kit.register("interact", ...) that OPENS it — registered so the binding is
        # machine-readable (probe/bindings/remap) while talkStep keeps the per-frame key reads.
        mode = "3d" if scheme.endswith("-3d") else "2d"
        interact = (_TEMPLATES / f"interact_{mode}.ts.tmpl").read_text(encoding="utf-8").rstrip("\n")
        interact_init = (_TEMPLATES / f"interact_init_{mode}.ts.tmpl").read_text(encoding="utf-8").rstrip("\n")
    game_dir(state.run_dir).mkdir(parents=True, exist_ok=True)
    main.write_text(render_template(tmpl, {"interact": interact, "interact_init": interact_init}),
                    encoding="utf-8")
    logger.info("control scaffold seeded (%s): %s", scheme or "no scheme", main)
