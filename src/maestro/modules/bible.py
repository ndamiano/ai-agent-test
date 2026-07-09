"""bible — the world-game root component. Authors the `bible` component.

The world-first analogue of `story`'s spine: a `setting` premise, the `factions` its conflicts run
between, and the `tensions` that pull the world (exactly one is the central `main` conflict). It is
build state derived from the frozen premise — authoring-only, like `story`: never lifted into the
IR (the runtime only ever sees the flags/nodes/places downstream modules produce), and cited by id
in other modules' prompts via `bible_block`.

Engine module, `selectable=False`: pulled in by the world-game aspects' `requires` (not yet — the
aspect layer lands with W3/W4); it composes cleanly on its own until then.

Example world:
  - "a drought-starved river barony, iron-age, superstitious" — bible + world + (later) quests
"""

from typing import Dict, List, Optional

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.module import Check, Error, Module, register_module


# ── structural validators (write-time, one slice at a time) ───────────────────
def v_faction(f: Dict) -> Optional[str]:
    """One faction — a named group with a want the world's tensions pull on."""
    if not isinstance(f, dict):
        return "a faction must be a JSON object"
    if not f.get("id"):
        return "a faction needs an 'id' (e.g. 'fac_guild')"
    for k in ("name", "wants"):
        if not f.get(k):
            return (f"faction {f.get('id')!r} is missing '{k}' — a faction needs a name and a "
                    f"'wants' (what it is after, in one clause)")
    return None


def v_tension(t: Dict) -> Optional[str]:
    """One tension — a conflict pulling the world, between declared factions. `between` refs are
    checked STRUCTURALLY here (a non-empty list of ids); that they resolve to real factions is the
    module's faction_refs check (a faction may be declared after the tension names it)."""
    if not isinstance(t, dict):
        return "a tension must be a JSON object"
    if not t.get("id"):
        return "a tension needs an 'id' (e.g. 'tension_levy')"
    if not t.get("summary"):
        return f"tension {t.get('id')!r} needs a 'summary' (one line: the concrete conflict)"
    if t.get("scale") not in ("main", "side"):
        return f"tension {t.get('id')!r} needs scale 'main' or 'side' (exactly one tension is 'main')"
    between = t.get("between")
    if not isinstance(between, list) or not between or not all(
            isinstance(x, str) and x for x in between):
        return (f"tension {t.get('id')!r} needs 'between': a non-empty list of the faction ids this "
                f"conflict runs between")
    return None


def v_bible(c: Dict) -> Optional[str]:
    """The DONE shape of the whole component (the schemas-validator for a write_component repair):
    a setting, unique-id factions, and unique-id tensions whose `between` all resolve, with exactly
    one 'main' tension. The slice tools build it incrementally and bypass this; it gates a full
    overwrite and expresses the terminal contract."""
    if not isinstance(c, dict):
        return "bible must be a JSON object"
    if not c.get("setting"):
        return "bible.setting must be a non-empty world premise"
    factions = c.get("factions")
    if not isinstance(factions, list):
        return "bible.factions must be a list"
    fids = set()
    for i, f in enumerate(factions):
        err = v_faction(f)
        if err:
            return f"bible.factions[{i}]: {err}"
        if f["id"] in fids:
            return f"bible.factions: duplicate faction id {f['id']!r}"
        fids.add(f["id"])
    tensions = c.get("tensions")
    if not isinstance(tensions, list):
        return "bible.tensions must be a list"
    tids = set()
    mains = 0
    for i, t in enumerate(tensions):
        err = v_tension(t)
        if err:
            return f"bible.tensions[{i}]: {err}"
        if t["id"] in tids:
            return f"bible.tensions: duplicate tension id {t['id']!r}"
        tids.add(t["id"])
        if t.get("scale") == "main":
            mains += 1
        missing = [x for x in t["between"] if x not in fids]
        if missing:
            return (f"bible.tensions[{i}] ({t['id']}): names undeclared faction(s) {missing} — "
                    f"declare them as factions")
    if mains != 1:
        return f"bible needs exactly one tension with scale='main' (found {mains})"
    return None


# ── authoring skeletons (small-model: skeleton first) ─────────────────────────
SKEL_BIBLE = (
    '// set_bible(setting, factions). Author the world premise + the factions its conflicts run\n'
    '// between (its tensions come later, one at a time):\n'
    '{\n'
    '  "setting": "one vivid line: the place, its tech level, the pressure on it — e.g.\n'
    '    \\"a drought-starved river barony, iron-age, superstitious\\"",\n'
    '  "factions": [   // 2-4 groups whose WANTS collide; the world\'s tensions run between them\n'
    '    {"id": "fac_guild", "name": "the Smith\'s Guild", "wants": "the baron\'s levy repealed"},\n'
    '    {"id": "fac_keep",  "name": "the Baron\'s men",   "wants": "order kept, the levy paid"}\n'
    '  ]\n'
    '}\n'
    '// Invent ids/names from THE REQUEST. Angle-bracket tokens are placeholders — never emit them.\n'
    '// If YOUR TARGET names one specific missing faction, just add THAT faction (add_faction).'
)

SKEL_TENSION_ONE = (
    '// tension_id (the tool arg) is the id, e.g. "tension_levy". `content` is this ONE tension:\n'
    '{\n'
    '  "summary": "one line: the concrete conflict pulling the world now — what is at stake",\n'
    '  "scale": "main | side",   // EXACTLY ONE tension across the world is \'main\' (the central one)\n'
    '  "between": ["fac_guild", "fac_keep"]   // the DECLARED faction ids this conflict runs between\n'
    '}\n'
    '// The first tension is the world\'s MAIN conflict (scale=\'main\'); the rest are \'side\'.\n'
    '//   Every id in `between` must be a faction already declared. A side tension may name one faction.'
)


# ── presentation block (this module owns how its component appears elsewhere) ──
def bible_block(artifact: Dict) -> List[str]:
    """The world bible rendered compactly for OTHER modules' prompts (the pattern of story_block).
    Bible ids are citable downstream — a resident's stance, a quest's tension — so ids are shown."""
    bible = artifact.get("bible") or {}
    setting = bible.get("setting")
    if not setting:
        return []
    out = ["", f"WORLD — setting: {setting}"]
    factions = [f for f in bible.get("factions") or [] if isinstance(f, dict) and f.get("id")]
    if factions:
        out.append("  factions:")
        out += [f"    {f['id']} — {f.get('name', '')}: wants {f.get('wants', '')}" for f in factions]
    tensions = [t for t in bible.get("tensions") or [] if isinstance(t, dict) and t.get("id")]
    if tensions:
        out.append("  tensions:")
        for t in tensions:
            between = ", ".join(t.get("between") or [])
            out.append(f"    {t['id']} ({t.get('scale', '?')}) [{between}]: {t.get('summary', '')}")
    return out


def _tensions(artifact: Dict) -> List[Dict]:
    return [t for t in (artifact.get("bible") or {}).get("tensions") or []
            if isinstance(t, dict) and t.get("id")]


def bible_view(artifact: Dict) -> Dict:
    """The slot-guard's view: the faction + tension ids already authored (no-overwrite keys). No
    open_slots — a tension's id is the model's to invent (not a dangling target)."""
    bible = artifact.get("bible") or {}
    return {"tension_ids": [t["id"] for t in _tensions(artifact)],
            "faction_ids": [f["id"] for f in bible.get("factions") or []
                            if isinstance(f, dict) and f.get("id")]}


def _one(_view) -> int:
    """Cap tension authoring at ONE per step — the tensions are a set the model builds coherently
    (each sees the priors, the main one first), like a storyline's beats."""
    return 1


_BIBLE_WRITE_TOOLS = frozenset({"set_bible", "add_faction", "read_component", "request_review"})
_TENSION_TOOLS = frozenset({"add_tension", "read_component", "request_review"})
_TENSION_GUARD = {"count_tool": "add_tension", "id_key": "tension_id",
                  "id_list_key": "tension_ids", "noun": "tension", "cap": _one}


def _d_tensions(chk, m, ctx):
    """Fan the tension shortfall to `min_tensions` into one add_tension job each (slot-guarded,
    sequential). The floor is the tension count; the SCALE (exactly one 'main') is a separate check."""
    need = ctx.param("min_tensions", 3) - len(_tensions(ctx.artifact))
    return checks.slot_errors(need, type=chk.tier, code=chk.code,
                              component="bible", noun="tension") if need > 0 else []


def _d_one_main(chk, m, ctx):
    """Exactly one tension is the world's central conflict. Suppressed while there are no tensions
    (the floor owns emptiness); a FIX once tensions exist — add_tension writes it as scale='main'
    (write-time already blocks a SECOND main, so this only ever repairs a missing one)."""
    tensions = _tensions(ctx.artifact)
    if not tensions:
        return []
    mains = [t for t in tensions if t.get("scale") == "main"]
    if len(mains) == 1:
        return []
    return [Error(type=chk.tier, code=chk.code, component="bible", path="scale#main",
                  message=(f"exactly one tension must be the world's MAIN conflict (scale='main'); "
                           f"found {len(mains)}. Add the central tension with "
                           f"add_tension(..., scale='main')."))]


def _d_faction_refs(chk, m, ctx):
    """Demand-driven (mirror inventory): every faction a tension's `between` names must be declared.
    One add_faction job per referenced-but-undeclared faction id, keyed on that id."""
    bible = ctx.artifact.get("bible") or {}
    fids = {f.get("id") for f in bible.get("factions") or [] if isinstance(f, dict)}
    out, seen = [], set()
    for t in _tensions(ctx.artifact):
        for ref in t.get("between") or []:
            if ref in fids or ref in seen:
                continue
            seen.add(ref)
            out.append(Error(type=chk.tier, code=chk.code, component="bible", path=ref, ref=ref,
                             kind="faction",
                             message=(f"tension {t['id']!r} names faction '{ref}' which isn't "
                                      f"declared — add it with add_faction('{ref}', ...).")))
    return out


class Bible(Module):
    id = "bible"
    layer = "engine"
    selectable = False
    description = ("The world's premise, factions, and the tensions between them — the root a "
                   "world game's places, residents, and quests all derive from.")
    priority = 5   # the world root: authored before cast (10) / story (30) / the realization modules
    component = "bible"
    mode_prompt = "bible_write.txt"
    mode_tools = _BIBLE_WRITE_TOOLS
    skeleton = SKEL_BIBLE
    schemas = {"bible": v_bible}
    skeletons = {"bible": SKEL_BIBLE}
    projector = staticmethod(bible_view)

    # setting first (blocking — the frame factions/tensions hang from), then the tensions ONE at a
    # time (each seeing the priors, the main one first), then the cheap structural safety: exactly
    # one main tension, and every tension's between resolves to a declared faction.
    checks = [
        Check("setting", lambda chk, m, ctx: m.wrap(chk, checks.exists(ctx.artifact, "bible.setting")),
              blocking=True, tools=_BIBLE_WRITE_TOOLS, prompt="bible_write.txt", skeleton=SKEL_BIBLE),
        Check("tension_floor", _d_tensions, tools=_TENSION_TOOLS, guard=_TENSION_GUARD,
              prompt="bible_tension_add.txt", skeleton=SKEL_TENSION_ONE),
        Check("one_main_tension", _d_one_main, job="fix", tools=_TENSION_TOOLS, guard=_TENSION_GUARD,
              prompt="bible_tension_add.txt", skeleton=SKEL_TENSION_ONE),
        Check("faction_refs", _d_faction_refs, job="fix", context=cr.ctx_structural,
              tools=_BIBLE_WRITE_TOOLS, prompt="bible_write.txt", skeleton=""),
    ]

    def params(self) -> Dict:
        return {"min_tensions": 3}

    def render_context(self, ctx: Dict) -> str:
        # Derived FROM the premise: title + concept + request + the world bible so far (setting +
        # factions + tensions already written), so the next tension continues the set coherently.
        art = ctx.get("artifact") or {}
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        lines += bible_block(art)
        lines += cr.tail_block(ctx)
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> List[str]:
        return bible_block(artifact)


MODULE = Bible()
register_module(MODULE)
