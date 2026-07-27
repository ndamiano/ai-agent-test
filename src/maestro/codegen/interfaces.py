"""The INTERFACE CONTRACT — the architecture the model writes for itself, before any code exists.

`runs/<id>/interfaces.json` declares, in the model's own words: every state field with its LIFETIME
and its ONE owner, every function with what it reads/writes/calls, and the whole-game invariants.
The frozen spec says what the game IS; this says how it is WIRED.

`manifest.json` and the GENERATED `game/state.ts` are derived from these declarations, so tsc
enforces them natively. The review phase (`build_steps.review_step`) runs before any code exists: a
contradiction here — a field two functions both own — costs one turn to patch in JSON and a whole
build to unpick once it has spread across a dozen modules.

This module owns the artifact: read/write, the patch ops the review applies, the review's slicing,
and the prompt-facing renderings (the state table + signatures the authoring turns read).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional

IFACE_FILE = "interfaces.json"

LIFETIMES = ("run", "session", "level", "turn")


# ── artifact ──────────────────────────────────────────────────────────────────
def path_of(run_dir) -> Path:
    return Path(run_dir) / IFACE_FILE


def load(run_dir) -> Optional[Dict]:
    p = path_of(run_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save(run_dir, iface: Dict) -> None:
    # Atomic save. A torn file strands builds
    enforce_kit_contract(run_dir, iface)
    p = path_of(run_dir)
    tmp = p.parent / f".{IFACE_FILE}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(iface, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def normalize(iface: Dict) -> Dict:
    """Coerce a model-authored architecture into the shape the rest of the pipeline indexes.

    A boundary, so it is forgiving: missing list keys become empty lists, and an entry without the
    key that NAMES it is dropped. Contradictions are the review phase's job, not this one's.
    """
    out = {
        "state": [f for f in _list(iface.get("state")) if f.get("field")],
        "functions": [f for f in _list(iface.get("functions")) if f.get("name")],
        "invariants": [s for s in _list(iface.get("invariants")) if isinstance(s, str)],
    }
    for f in out["functions"]:
        # A game is a FLAT folder. `systems/beat.ts` is a reasonable intent the tools cannot honour —
        # write flattens the path, so the manifest would ask forever for a file that lands elsewhere.
        if f.get("file"):
            f["file"] = str(f["file"]).replace("\\", "/").split("/")[-1]
    for f in out["state"]:
        for k in ("mutators", "readers"):
            f[k] = [x for x in _list(f.get(k)) if isinstance(x, str)]
    for f in out["functions"]:
        for k in ("reads", "writes", "calls"):
            f[k] = [x for x in _list(f.get(k)) if isinstance(x, str)]
    if iface.get("reviewed"):
        out["reviewed"] = True
    return out


def _list(v) -> List:
    return v if isinstance(v, list) else []


# ── the kit's law: signatures the architecture does not get to choose ─────────
# main.ts is GENERATED and calls the hooks with fixed arity; the game draws through DrawApi, never
# the DOM. Both are read from engine.d.ts and written over the declaration on every save.
_DOM_DRAW_TYPES = {
    "CanvasRenderingContext2D": "DrawApi",
    "OffscreenCanvasRenderingContext2D": "DrawApi",
    "CanvasRenderingContext": "DrawApi",
    "HTMLCanvasElement": "DrawApi",
}

# The state entries the scaffold's own code decides. state.ts is GENERATED from this table, so an
# architecture that omits a field main.ts asserts describes a GameState the game cannot be written
# against: the model widens GameState locally to compile, the declaration stops describing the real
# state, and the next file authored against the generated type dies on it.
_SCAFFOLD_STATE = {
    "player": {"type": "Entity", "lifetime": "run", "owner": "init",
               "meaning": "the avatar the control scaffold steers; init must spawn it",
               "mutators": ["init", "update"], "readers": []},
}

# The state the KIT ITSELF reads and writes — not the architecture's to omit. The engine renders the
# scene from state.world (an entity is visible because it is IN there), and kit.talkOpen/kit.quest.*
# ASSIGN state.talk / state.quests directly. A GameState missing one forbids the game from calling
# the primitive its own spec composed, and no edit to a game file can fix that: a measured build
# declared neither `world` nor `talk`, and burned 90 steps on tsc blaming game.ts for using both.
# `optional` because the kit creates them on demand — only `world` must exist from frame 0. A field
# whose primitive this spec never composes is NOT declared: an unused row in the state table is one
# more thing for a small model to build to.
_KIT_STATE = {
    "world": {"type": "World", "lifetime": "run", "owner": "init", "uses": (),
              "meaning": "every entity the engine renders; init spawns into it",
              "mutators": ["init", "update"], "readers": []},
    "talk": {"type": "Talk", "lifetime": "turn", "owner": "update", "optional": True,
             "uses": ("dialogue", "shop"),
             "meaning": "the open dialogue, written by kit.talkOpen and cleared by kit.talkStep",
             "mutators": ["update"], "readers": ["hud"]},
    "focus": {"type": "Entity", "lifetime": "turn", "owner": "update", "optional": True,
              "uses": ("dialogue", "shop", "quest"),
              "meaning": "what the activate key would act on, chosen by kit.focus each frame",
              "mutators": ["update"], "readers": ["hud"]},
    "quests": {"type": "Quest[]", "lifetime": "run", "owner": "init", "optional": True,
               "uses": ("quest",),
               "meaning": "the quest log kit.quest.add/complete maintains",
               "mutators": ["init", "update"], "readers": ["hud"]},
}
_HOOKS_IFACE_RE = re.compile(r"interface\s+GameHooks<(\w+)>\s*\{(.*?)\n\}", re.S)
_HOOK_MEMBER_RE = re.compile(r"^\s*(\w+)\??\s*(\([^;]*\)\s*:\s*[^;]+);", re.M)
_ENGINE_DTS = "engine.d.ts"


def _engine_dts(run_dir) -> str:
    """The kit types as tsc sees them: the run's own copy, falling back to the repo's master."""
    from maestro.codegen.gates import game_dir

    for p in (game_dir(run_dir) / _ENGINE_DTS,
              Path(__file__).resolve().parents[3] / "runtime" / _ENGINE_DTS):
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def hook_signatures(run_dir) -> Dict[str, str]:
    """name -> the hook's signature, parsed from engine.d.ts with the state type substituted in."""
    m = _HOOKS_IFACE_RE.search(_engine_dts(run_dir))
    if not m:
        return {}
    var, body = m.group(1), m.group(2)
    out = {}
    for name, rest in _HOOK_MEMBER_RE.findall(body):
        sig = re.sub(rf"\b{re.escape(var)}\b", "GameState", " ".join(rest.split()))
        out[name] = f"{name}{sig}"
    return out


def kit_types(run_dir) -> List[str]:
    """The type names engine.d.ts declares — the vocabulary an architecture may name."""
    return sorted(set(re.findall(r"^\s*(?:declare\s+)?(?:interface|type)\s+(\w+)",
                                 _engine_dts(run_dir), re.M)))


def _retype(text: str) -> str:
    for dom, kit in _DOM_DRAW_TYPES.items():
        text = re.sub(rf"\b{dom}\b", kit, text)
    return text


def enforce_kit_contract(run_dir, iface: Dict) -> List[str]:
    """Overwrite the parts of the architecture the kit already decides. Returns what it corrected."""
    from maestro.codegen.scaffold import ENTRY_HOOK, unguarded_state_fields

    hooks = hook_signatures(run_dir)
    fixed: List[str] = []
    for fn in _list(iface.get("functions")):
        sig = fn.get("signature") or ""
        want = hooks.get(fn.get("name")) if (fn.get("file") or ENTRY_HOOK) == ENTRY_HOOK else None
        if want and " ".join(sig.split()) != want:
            fn["signature"] = want
            fixed.append(f"{fn['name']}: hook signature is the kit's — {want}")
            continue
        if _retype(sig) != sig:
            fn["signature"] = _retype(sig)
            fixed.append(f"{fn['name']}: the game draws through DrawApi, not the DOM")
    for f in _list(iface.get("state")):
        if _retype(f.get("type") or "") != (f.get("type") or ""):
            f["type"] = _retype(f["type"])
            fixed.append(f"{f['field']}: the game draws through DrawApi, not the DOM")
    declared = {f.get("field") for f in _list(iface.get("state"))}
    for name in unguarded_state_fields(run_dir):
        entry = _SCAFFOLD_STATE.get(name)
        if not entry or name in declared:
            continue
        iface.setdefault("state", []).append({"field": name, **entry})
        fixed.append(f"{name}: main.ts asserts state.{name} — declared {entry['type']}, "
                     f"owned by {entry['owner']}()")
    uses = _spec_uses(run_dir)
    for name, entry in _KIT_STATE.items():
        wanted = entry["uses"]
        if name in declared or (wanted and not (set(wanted) & uses)):
            continue
        iface.setdefault("state", []).append({"field": name, **{k: v for k, v in entry.items()
                                                                if k != "uses"}})
        fixed.append(f"{name}: the kit reads and writes state.{name} — declared {entry['type']}")
    return fixed


def _spec_uses(run_dir) -> set:
    """The catalog blocks the frozen spec composes, lowercased. Read off spec.json: `save` is called
    from paths that hold no spec, and the run dir is where the spec durably lives."""
    p = Path(run_dir) / "spec.json"
    if not p.exists():
        return set()
    design = (json.loads(p.read_text(encoding="utf-8")) or {}).get("design") or {}
    return {str(u).lower() for u in _list(design.get("uses"))}


def hooks_block(run_dir, spec: Optional[Dict] = None) -> str:
    """The fixed hook signatures + the kit's type vocabulary, for the architecture turn.

    Mode-filtered: a 3D game has no draw (the renderer draws from entity shape tags), so listing one
    would contradict the rule the same prompt states two lines earlier.
    """
    from maestro.codegen.module import _hook_exports
    from maestro.codegen.scaffold import is_3d, required_state_fields, state_contract_notes

    hooks = hook_signatures(run_dir)
    if not hooks:
        return ""
    wanted = _hook_exports(spec or {})
    # A place in a 3D game has three coordinates. Declared as Vec2 the whole game is authored
    # against a type with no `z`, and every site that needs one is a tsc error no edit can settle.
    dims = ("\n\n# THIS GAME IS 3D — a position in the world is Vec3 {x, y, z}. Vec2 has no z.\n"
            if is_3d(spec or {}) else "")
    return ("\n\n# THE ENTRY HOOKS — main.ts is GENERATED and calls these. Their signatures are FIXED;\n"
            "# declare them EXACTLY as written, on game.ts.\n"
            + "\n".join(f"  {s}" for n, s in hooks.items() if n in wanted)
            + "\n\n# STATE THE SCAFFOLD STEERS — main.ts already drives the game through these fields.\n"
              "# Declare every one your game uses, spelled exactly like this. `player` and `world` are\n"
              "# not optional: init must create state.player as an entity spawned into state.world, and\n"
              "# the game crashes at frame 0 without it. The TYPE each one must have is not yours to\n"
              "# choose either — the scaffold calls them directly:\n  "
            + ", ".join(required_state_fields(spec or {}))
            + "\n"
            + "\n".join(f"    {n}" for n in state_contract_notes(spec or {}))
            + "\n\n# THE KIT'S TYPES — the only type names you may name besides your own and TypeScript's\n"
              "# builtins. They are AMBIENT: they already exist everywhere, so never import them — not\n"
              "# from './state.ts', not from a 'kit' module, not as members of a Kit namespace.\n"
              "# There is no DOM either: the game never sees a canvas or a rendering context.\n  "
            + ", ".join(kit_types(run_dir))
            + dims)


# ── prompt renderings ─────────────────────────────────────────────────────────
def state_table(iface: Dict) -> str:
    """The ownership table every authoring/fix turn reads — one line per field, its whole contract."""
    lines = []
    for f in _list(iface.get("state")):
        muts = ", ".join(f.get("mutators") or []) or "none"
        lines.append(f"  {f.get('field')} : {f.get('type', '?')}"
                     f" | lifetime={f.get('lifetime', '?')} | owner={f.get('owner', '?')}"
                     f" | may mutate in place: {muts}"
                     f" | {f.get('meaning', '')}")
    return "\n".join(lines) or "  (no state declared)"


def signatures(iface: Dict, exclude: str = "") -> str:
    lines = [f"  {f.get('signature') or f['name'] + '(...)'}"
             for f in _list(iface.get("functions")) if f.get("name") != exclude]
    return "\n".join(lines) or "  (none)"


STATE_FILE = "state.ts"
_STATE_HEADER = ("// GENERATED from interfaces.json — never edit by hand; amend the architecture "
                 "instead.\n")


def _type_tree(fields: List[Dict]) -> Dict:
    """Nest dotted field names (`fight.hand`) into the object shape they describe."""
    tree: Dict = {}
    for f in fields:
        parts = str(f["field"]).split(".")
        node = tree
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                break
        if isinstance(node, dict):
            node[parts[-1]] = f
    return tree


def _render_type(node, indent: int) -> str:
    pad = "  " * indent
    out = ["{"]
    for name, child in node.items():
        if isinstance(child, dict) and "field" not in child:
            out.append(f"{pad}  {name}: {_render_type(child, indent + 1)};")
            continue
        note = " ".join(x for x in [child.get("meaning", ""),
                                    f"(lifetime={child.get('lifetime', '?')}, "
                                    f"owner={child.get('owner', '?')})"] if x)
        out.append(f"{pad}  /** {note} */")
        opt = "?" if child.get("optional") else ""
        out.append(f"{pad}  {name}{opt}: {child.get('type') or 'any'};")
    out.append(pad + "}")
    return "\n".join(out)


def generate_state_ts(run_dir, iface: Dict) -> None:
    """Emit `game/state.ts` — the declared state table as a real TypeScript interface.

    Without this the architecture is advisory: a file authored early writes the fields the state
    table declares, the entry file authored later invents its own GameState, and tsc blames the
    early file for a mismatch neither side owns. One generated type makes the declaration binding,
    the same way data.ts makes the data manifest binding.
    """
    from maestro.codegen.gates import game_dir

    fields = [f for f in _list(iface.get("state")) if f.get("field")]
    body = _render_type(_type_tree(fields), 0) if fields else "{\n  [key: string]: any;\n}"
    out = game_dir(run_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / STATE_FILE).write_text(
        f"{_STATE_HEADER}\nexport interface GameState {body}\n", encoding="utf-8")


def state_ts_block(run_dir) -> str:
    from maestro.codegen.gates import game_dir

    p = game_dir(run_dir) / STATE_FILE
    if not p.exists():
        return ""
    # The authoring turn is told to import the types it needs from the file that owns them. Kit
    # types are owned by no file — they are ambient — so that instruction generalizes into
    # `import { Kit, Input } from "./state.ts"` and, when that fails, an invented `../globals.ts`.
    # Naming the ambient set HERE is what stops it: this block is the one both the authoring and the
    # fix turns read, and it is attached to the very file being imported from.
    return ("# THE STATE TYPE — GENERATED from your architecture, in ./state.ts. Import it; never\n"
            "# redeclare GameState or invent a field that is not on it. To change the shape, the\n"
            "# architecture is what changes.\n"
            "# ./state.ts exports GameState AND NOTHING ELSE. These names are AMBIENT — they exist\n"
            "# everywhere already, so importing them from any file is an error:\n"
            f"#   {', '.join(kit_types(run_dir))}\n"
            f"```ts\n{p.read_text(encoding='utf-8')}\n```")


def manifest_from(iface: Dict, run_dir, hook_exports: List[str]) -> Dict:
    """The code manifest, derived from the architecture.

    GENERATED files on disk are dropped — the tools refuse to author them, so listing one strands an
    authoring step. The entry hook is appended if the architecture omitted it, since the scaffold
    imports it either way.
    """
    from maestro.codegen.gates import game_files
    from maestro.codegen.scaffold import ENTRY_HOOK

    generated = {n for n, src in game_files(run_dir).items()
                 if src.lstrip().startswith("// GENERATED")}
    grouped: Dict[str, List[Dict]] = {}
    for fn in _list(iface.get("functions")):
        if fn.get("file"):
            grouped.setdefault(fn["file"], []).append(fn)

    files = []
    for name, fns in grouped.items():
        if name in generated:
            continue
        outs = [f["name"] for f in fns]
        if name == ENTRY_HOOK:
            outs = list(dict.fromkeys(outs + hook_exports))
        purpose = "; ".join(f["purpose"] for f in fns if f.get("purpose"))[:200]
        files.append({"name": name, "purpose": purpose, "exports": outs})
    if ENTRY_HOOK not in generated and not any(f["name"] == ENTRY_HOOK for f in files):
        files.append({"name": ENTRY_HOOK, "purpose": "the game behind the scaffold hooks",
                      "exports": list(hook_exports)})
    return {"files": files}


def contract_of(iface: Dict, name: str) -> Optional[Dict]:
    return next((f for f in _list(iface.get("functions")) if f.get("name") == name), None)


def functions_in(iface: Dict, file: str) -> List[Dict]:
    return [f for f in _list(iface.get("functions")) if f.get("file") == file]


def interfaces_block(iface: Optional[Dict]) -> str:
    """The architecture block injected into authoring + fix turns."""
    if not iface:
        return ""
    return ("# THE ARCHITECTURE YOU DECLARED — these contracts are binding\n\n"
            "## STATE — every field, its lifetime, and the ONE function allowed to REPLACE it\n"
            f"{state_table(iface)}\n\n"
            "A field you do not own may only be modified IN PLACE (push/splice/+=), and only if you "
            "are listed under 'may mutate in place'. A lifetime=run field must survive the whole "
            "run — never rebuild one from scratch.\n\n"
            "## FUNCTIONS — these exist; call them, never reimplement them\n"
            f"{signatures(iface)}"
            + ("\n\n## WHOLE-GAME INVARIANTS\n" + "\n".join(f"  - {s}" for s in iface["invariants"])
               if iface.get("invariants") else ""))


# ── review: patch ops ─────────────────────────────────────────────────────────
# Re-emitting the whole architecture to change one entry is what blows the token cap on any large
# game (a 38-function RPG is ~28KB of JSON), so the review edits by OP instead.
# A state entry is only a contract while it carries these. A replace swaps the WHOLE entry, so an
# omitted key is a silent deletion — one measured review dropped `owner` from all 9 fields, so the
# generated state.ts documented no owner for any of them for the rest of the build.
_STATE_KEYS = ("field", "type", "lifetime", "owner")
STATE_OPS = {"replace_state": "replace", "delete_state": "delete", "add_state": "add"}
FUNC_OPS = {"replace_function": "replace", "delete_function": "delete", "add_function": "add"}
OP_LIST = ", ".join(sorted(list(STATE_OPS) + list(FUNC_OPS) + ["set_invariants"]))


def apply_patches(iface: Dict, patches: List, dry: bool = False) -> List[str]:
    """Apply review patch ops in place. Returns the ops that could NOT be applied, worded as
    instructions the model can act on — a rejected op is re-offered once with these as feedback.

    `dry=True` mutates nothing: the caller validates a whole patch set before committing to it, so a
    batch that is half-wrong never lands half-applied.
    """
    errs: List[str] = []
    for p in patches:
        if not isinstance(p, dict):
            errs.append(f"a patch is not an object: {str(p)[:80]}")
            continue
        op = p.get("op")
        if op == "set_invariants":
            if not isinstance(p.get("value"), list):
                errs.append("set_invariants: value must be a list of strings")
            elif not dry:
                iface["invariants"] = p["value"]
            continue
        if op in STATE_OPS:
            lst, idk, kind = iface.setdefault("state", []), "field", STATE_OPS[op]
        elif op in FUNC_OPS:
            lst, idk, kind = iface.setdefault("functions", []), "name", FUNC_OPS[op]
        else:
            errs.append(f"unknown op {op!r}. Use one of: {OP_LIST}")
            continue
        val = p.get("value")

        def incomplete():
            """A state entry is only a contract while it carries every key; replace swaps the WHOLE
            entry, so an omitted one is a silent deletion."""
            if op not in STATE_OPS or not isinstance(val, dict):
                return None
            gaps = [k for k in _STATE_KEYS if not val.get(k)]
            return (f"{op}: the value is the COMPLETE entry — it is missing {', '.join(gaps)}"
                    if gaps else None)

        if kind == "add":
            if not isinstance(val, dict) or not val.get(idk):
                errs.append(f"{op}: value must be a complete object with a {idk!r} key")
            elif any(x.get(idk) == val[idk] for x in lst):
                errs.append(f"{op}: '{val[idk]}' already exists — use a replace op instead")
            elif incomplete():
                errs.append(incomplete())
            elif not dry:
                lst.append(val)
            continue
        key = p.get(idk) or (val or {}).get(idk)
        if not key:
            errs.append(f"{op}: missing the {idk!r} key naming what to change")
            continue
        at = next((i for i, x in enumerate(lst) if x.get(idk) == key), None)
        if at is None:
            errs.append(f"{op}: no {idk} named '{key}' exists in the architecture")
        elif kind == "delete":
            if not dry:
                del lst[at]
        elif not isinstance(val, dict) or not val.get(idk):
            errs.append(f"{op}: value must be the COMPLETE replacement object, with its {idk!r} key")
        elif incomplete():
            errs.append(incomplete())
        elif not dry:
            lst[at] = val
    return errs


# ── review: slicing + dedup ───────────────────────────────────────────────────
SLICE_SIZE = 8
_SLICE_THRESHOLD = 12


def slices(iface: Dict, chunk: int = SLICE_SIZE) -> List[Dict]:
    """Review a large architecture a few functions at a time. State, data and invariants stay WHOLE in
    every slice — they are what the functions are judged against; only the function list is split."""
    fns = _list(iface.get("functions"))
    if len(fns) <= _SLICE_THRESHOLD:
        return [iface]
    return [{**iface, "functions": fns[i:i + chunk]} for i in range(0, len(fns), chunk)]


REVIEW_LOG = "review_log.jsonl"


def log_review(run_dir, round_no: int, problems: List, patches: List, rejected: List[str]) -> None:
    """Append what a review round found and what it changed.

    The review patches the architecture in place, so without this the only trace of a round is a
    count in the build log — "which contradiction did it fix, and did the fix land?" is otherwise
    unanswerable once the run is over.
    """
    line = {"round": round_no,
            "problems": [p if isinstance(p, dict) else {"problem": str(p)} for p in problems],
            "patches": patches, "rejected": rejected}
    with (Path(run_dir) / REVIEW_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def problem_key(p) -> str:
    """A stable key for a reported problem, so the same complaint reported in two rounds (or two
    slices of one round) is patched once. Prose varies; the normalized head does not."""
    t = p.get("problem", "") if isinstance(p, dict) else str(p)
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()[:80]


def problem_text(p) -> str:
    return str(p.get("problem", p) if isinstance(p, dict) else p)[:200]
