"""cast

This module defines the characters present in the game. It gives them a voice, description and
history, which other modules can use to build upon.

Example games:
  - "two estranged brothers clear out their dead father's garage" — cast + story + scenes
  - "escape a flooding lighthouse, talking past its keeper"        — cast + world + scenes + inventory
"""

from typing import Dict, Optional

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


def v_characters(c: Dict) -> Optional[str]:
    chars = c.get("characters")
    if not isinstance(chars, list) or not chars:
        return "characters.characters must be a non-empty list of character objects"
    for i, ch in enumerate(chars):
        if not isinstance(ch, dict):
            return f"characters.characters[{i}] must be an object"
        if not ch.get("id"):
            return f"characters.characters[{i}] needs an 'id' (snake_case, e.g. 'evelyn')"
        if not ch.get("name"):
            return f"characters.characters[{i}] needs a 'name'"
    return None


SKEL_CHARACTERS = (
    '{\n'
    '  "characters": [\n'
    '    {\n'
    '      "id": "<snake_case_id>",\n'
    '      "name": "<Display Name>",\n'
    '      "role": "protagonist | antagonist | npc",\n'
    '      "voice": "one line: vocabulary, sentence length, rhythm, what breaks under pressure",\n'
    '      "sex": "male|female — drives sprite and spoken-voice casting",\n'
    '      "temperament": "2-4 words for how they carry themselves",\n'
    '      "drive": "what they want, plainly — a thing they would say out loud, NOT a goal for this plot",\n'
    '      "history": ["one concrete formative event", "a second, different event"],\n'
    '      "competencies": ["a concrete skill", "another"],\n'
    '      "example_lines": ["a flat line about something domestic or logistical — food, sleep, a ride, a chore, an object in the room — NOT about work; the content forgettable", "a reactive line that ENDS IN AN ACTION OR DEMAND, not an observation — they complain AT someone or threaten to do something, never reflect on what a thing means"],\n'
    '      "color": "#c8ffc8"\n'
    '    }\n'
    '  ]\n'
    '}\n'
    '// Invent ids/names/detail from THE REQUEST. Angle-bracket tokens are placeholders —\n'
    '//   never emit them literally, and do not reuse example ids from other prompts.\n'
    '// role: exactly one protagonist drives the story; an antagonist opposes them (give it a\n'
    '//   drive that COLLIDES with the protagonist\'s); everyone else is an npc.\n'
    '// drive: legible beats exotic. history: 2-3 DISTINCT whole events, each new info.\n'
    '// example_lines: must sound DIFFERENT between characters — swap-test them.'
)


class Cast(Module):
    id = "cast"
    description = ("Named characters with role, backstory, voice, and temperament. Include whenever "
                   "the game has people who speak or act.")
    priority = 10
    component = "characters"
    mode_prompt = "characters_write.txt"
    mode_tools = frozenset({"write_component", "update_scratchpad", "request_review"})
    skeleton = SKEL_CHARACTERS
    schemas = {"characters": v_characters}
    skeletons = {"characters": SKEL_CHARACTERS}

    checks = [
        Check("min_characters", lambda chk, m, ctx: m.wrap(chk, checks.count(
            ctx.artifact, "characters.characters", min=ctx.param("min_characters", 1)))),
        Check("character_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "characters.characters",
            fields=ctx.param("character_fields", ["id", "name"])))),
        Check("distinct_characters", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "characters.characters", key="id"))),
    ]

    def params(self) -> Dict:
        return {"min_characters": 1, "character_fields": ["id", "name"]}

    def render_context(self, ctx: Dict) -> str:
        # The cast is the source every later scene draws from — author it from the bare request, not
        # spec/skeleton noise (the shape rides on the system prompt): concept + request + to-do.
        spec = ctx.get("spec", {}) or {}
        out = [f"TITLE: {spec.get('title', '')}", "",
               f"CONCEPT: {spec.get('concept', '')}", "",
               f"REQUEST: {spec.get('request', '')}"]
        todo = [f"- {e.code}: {e.message}" for e in ctx.get("todo", [])
                if e.component == "characters"]
        if todo:
            out += ["", "TO-DO (failing done-conditions for characters):", *todo]
        return "\n".join(out)


MODULE = Cast()
register_module(MODULE)
