"""cast

This module defines the characters present in the game. It gives them a voice, description and
history, which other modules can use to build upon.

Example games:
  - "two estranged brothers clear out their dead father's garage" — cast + story + scenes
  - "escape a flooding lighthouse, talking past its keeper"        — cast + world + scenes + inventory
"""

from typing import Dict, Optional

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.module import Check, Module, register_module

def character_cards(artifact: Dict, only=None) -> list:
    """Full RP-style character cards — how the cast presents itself in OTHER modules' prompts
    (dialogue/story/combat authors). Bounded by cast size; never trimmed (a card with no history
    writes a person with no past)."""
    chars = [c for c in (artifact.get("characters") or {}).get("characters", [])
             if isinstance(c, dict) and c.get("id") and (only is None or c["id"] in only)]
    if not chars:
        return []
    out = ["", "CHARACTERS (use these EXACT ids as speakers; write each person from their card):"]
    for c in chars:
        out.append(f"  {c['id']} — {c.get('name', '')} ({c.get('role', 'npc')})")
        for key in ("voice", "temperament", "drive"):
            if c.get(key):
                out.append(f"    {key}: {c[key]}")
        for key in ("history", "competencies"):
            for v in c.get(key) or []:
                out.append(f"    {key}: {v}")
        for ln in c.get("example_lines") or []:
            out.append(f'    says: "{ln}"')
    return out


def character_index(artifact: Dict) -> list:
    """Ids only — id + name + role, no cards. What a crossref/structural fix needs to name a real
    character; the full `character_cards` is reserved for authoring calls that write a person."""
    chars = [c for c in (artifact.get("characters") or {}).get("characters", [])
             if isinstance(c, dict) and c.get("id")]
    if not chars:
        return []
    return ["", "CHARACTERS (these EXACT ids):",
            *(f"  {c['id']} — {c.get('name', '')} ({c.get('role', 'npc')})" for c in chars)]


def v_character_one(ch: Dict) -> Optional[str]:
    """Structural gate for ONE character — shared by add_character (per-person) and v_characters
    (whole-list fixes). Enum-gate sex at write time: the compile gate also checks it, but by then
    the component is done-locked and the compile fixer can't touch it (observed deadlock:
    sex 'female (Sarah), male (Tom)' — two people stuffed into one card)."""
    if not isinstance(ch, dict):
        return "a character must be a JSON object"
    if not ch.get("id"):
        return "a character needs an 'id' (snake_case, e.g. 'evelyn')"
    if not ch.get("name"):
        return "a character needs a 'name'"
    sex = ch.get("sex")
    if sex is not None and sex not in ("male", "female"):
        return (f"character.sex must be exactly 'male' or 'female' — got {sex!r}. "
                f"One character per entry; never combine people in one card.")
    return None


def v_characters(c: Dict) -> Optional[str]:
    chars = c.get("characters")
    if not isinstance(chars, list) or not chars:
        return "characters.characters must be a non-empty list of character objects"
    for i, ch in enumerate(chars):
        err = v_character_one(ch)
        if err:
            return f"characters.characters[{i}]: {err}"
    return None


def cast_view(artifact: Dict) -> Dict:
    """The slot-guard's view — the cast ids already authored. No open_slots: a character's id is
    the model's to invent (unlike a scene, whose slot id is a dangling target), so parallel
    siblings are differentiated by the 'author #N' note, not an assigned id."""
    chars = [c for c in (artifact.get("characters") or {}).get("characters", [])
             if isinstance(c, dict) and c.get("id")]
    return {"character_ids": [c["id"] for c in chars]}


SKEL_CHARACTER_ONE = (
    '// character_id (the tool arg) is the snake_case id. `content` is this ONE person:\n'
    '{\n'
    '  "name": "<Display Name>",\n'
    '  "role": "protagonist | antagonist | npc",\n'
    '  "voice": "one line: vocabulary, sentence length, rhythm, what breaks under pressure",\n'
    '  "sex": "male|female — drives sprite and spoken-voice casting",\n'
    '  "temperament": "2-4 words for how they carry themselves",\n'
    '  "drive": "what they want, plainly — a thing they would say out loud, NOT a goal for this plot",\n'
    '  "history": ["one concrete formative event", "a second, different event"],\n'
    '  "competencies": ["a concrete skill", "another"],\n'
    '  "example_lines": ["a flat line about something domestic or logistical — food, sleep, a ride, a chore, an object in the room — NOT about work; the content forgettable", "a reactive line that ENDS IN AN ACTION OR DEMAND, not an observation — they complain AT someone or threaten to do something, never reflect on what a thing means"],\n'
    '  "color": "#c8ffc8"\n'
    '}\n'
    '// Invent ids/names/detail from THE REQUEST. Angle-bracket tokens are placeholders —\n'
    '//   never emit them literally, and do not reuse example ids from other prompts.\n'
    '// role: exactly one protagonist across the whole cast; an antagonist opposes them (give it a\n'
    '//   drive that COLLIDES with the protagonist\'s); everyone else is an npc.\n'
    '// drive: legible beats exotic. history: 2-3 DISTINCT whole events, each new info.\n'
    '// example_lines: must sound DIFFERENT from the people already listed — swap-test them.'
)

_ADD_TOOLS = frozenset({"add_character", "read_component", "request_review"})
# The post-authoring safety repairs (missing field, dup id) round-trip through write_component:
# there is no per-character edit tool, and add_character refuses an existing id, so the fix reads
# the cast then rewrites it with ONE change. The specific prompts below forbid a clobber-rewrite.
_REPAIR_TOOLS = frozenset({"read_component", "write_component", "request_review"})
_CAST_GUARD = {"count_tool": "add_character", "id_key": "character_id",
               "id_list_key": "character_ids", "noun": "character"}


def _d_min_characters(chk, m, ctx):
    """A cast shortfall fans into one per-person create-error — the loop authors one character per
    step (slot-guarded add_character), each seeing the ones already written."""
    need = ctx.param("min_characters", 1) - checks.length(ctx.artifact, "characters.characters")
    return checks.slot_errors(need, type=chk.tier, code=chk.code,
                              component="characters", noun="character") if need > 0 else []


class Cast(Module):
    id = "cast"
    description = ("Named characters with role, backstory, voice, and temperament. Include whenever "
                   "the game has people who speak or act.")
    priority = 10
    component = "characters"
    mode_prompt = "characters_write.txt"
    mode_tools = frozenset({"write_component", "read_component", "request_review"})
    skeleton = SKEL_CHARACTER_ONE
    schemas = {"characters": v_characters}
    skeletons = {"characters": SKEL_CHARACTER_ONE}
    projector = staticmethod(cast_view)

    # Author the cast ONE person at a time (slot-guarded add_character), each with the already-written
    # cast in context so it differs and forms an ensemble. The field/distinct checks are cheap
    # post-authoring safety — a per-person add validates structure + the guard blocks dup ids, so they
    # rarely fire; when they do, the fix rewrites the whole list.
    checks = [
        Check("min_characters", _d_min_characters, tools=_ADD_TOOLS, guard=_CAST_GUARD,
              prompt="characters_add.txt", skeleton=SKEL_CHARACTER_ONE),
        Check("character_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "characters.characters",
            fields=ctx.param("character_fields", ["id", "name"]))), context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="cast_field_patch.txt", skeleton=""),
        Check("distinct_characters", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "characters.characters", key="id")), context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="cast_rename_duplicate.txt", skeleton=""),
    ]

    def params(self) -> Dict:
        return {"min_characters": 1, "character_fields": ["id", "name"]}

    def self_digest(self, artifact: Dict) -> list:
        return character_index(artifact)

    def render_context(self, ctx: Dict) -> str:
        # The cast is the source every later scene draws from — author it from the bare request, not
        # spec/skeleton noise (the shape rides on the system prompt): concept + request + the cast
        # already written (so the next person is DIFFERENT and belongs with them) + the target.
        from maestro import context_render as cr
        spec = ctx.get("spec", {}) or {}
        art = ctx.get("artifact") or {}
        out = [f"TITLE: {spec.get('title', '')}", "",
               f"CONCEPT: {spec.get('concept', '')}", "",
               f"REQUEST: {spec.get('request', '')}"]
        existing = [c for c in (art.get("characters") or {}).get("characters", [])
                    if isinstance(c, dict) and c.get("id")]
        if existing:
            out += character_cards(art)
            out += ["", "Those people are ALREADY written — do not repeat or rewrite them. Author a "
                    "DIFFERENT character who belongs in this cast with them."]
        out += cr.target_block(ctx)
        return "\n".join(out)


MODULE = Cast()
register_module(MODULE)
