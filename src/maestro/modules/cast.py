"""cast — the character roster. Authors `premise`.

Owns the premise checks: it has the write_component tool, so every premise failure is fixable here.
The *size* of those checks is param-driven — a VN raises `min_characters`/`min_endings`/the rich
`premise_fields` floor via dialogue's params(); cast reads the resolved value and emits the check.
A point-and-click leaves the floors low, so its NPC premise only needs id + name.
"""

from typing import Dict, List, Optional, Tuple

from maestro.modules import checks
from maestro.modules.module import Error, ErrorType, Module, register_module

_PREMISE_KEEP = ("id", "name", "voice", "temperament", "drive", "example_lines")


def v_premise(c: Dict) -> Optional[str]:
    chars = c.get("characters")
    if not isinstance(chars, list) or not chars:
        return "premise.characters must be a non-empty list of character objects"
    for i, ch in enumerate(chars):
        if not isinstance(ch, dict):
            return f"premise.characters[{i}] must be an object"
        if not ch.get("id"):
            return f"premise.characters[{i}] needs an 'id' (snake_case, e.g. 'evelyn')"
        if not ch.get("name"):
            return f"premise.characters[{i}] needs a 'name'"
    return None


SKEL_PREMISE = (
    '{\n'
    '  "central_question": "the dramatic question the endings answer differently",\n'
    '  "characters": [\n'
    '    {\n'
    '      "id": "<snake_case_id>",\n'
    '      "name": "<Display Name>",\n'
    '      "voice": "one line: vocabulary, sentence length, rhythm, what breaks under pressure",\n'
    '      "temperament": "2-4 words for how they carry themselves",\n'
    '      "drive": "what they want, plainly — a thing they would say out loud, NOT a goal for this plot",\n'
    '      "history": ["one concrete formative event", "a second, different event"],\n'
    '      "competencies": ["a concrete skill", "another"],\n'
    '      "example_lines": ["a flat line about something domestic or logistical — food, sleep, a ride, a chore, an object in the room — NOT about work; the content forgettable", "a reactive line that ENDS IN AN ACTION OR DEMAND, not an observation — they complain AT someone or threaten to do something, never reflect on what a thing means"],\n'
    '      "color": "#c8ffc8"\n'
    '    }\n'
    '  ],\n'
    '  "endings": [ {"id": "ending_<slug>", "description": "..."} ]\n'
    '}\n'
    '// Invent ids/names/detail from THE REQUEST. Angle-bracket tokens are placeholders —\n'
    '//   never emit them literally, and do not reuse example ids from other prompts.\n'
    '// drive: legible beats exotic. history: 2-3 DISTINCT whole events, each new info.\n'
    '// example_lines: must sound DIFFERENT between characters — swap-test them.'
)


class Cast(Module):
    id = "cast"
    description = ("Named characters with backstory, voice, and temperament. Include whenever the "
                   "game has people who speak or act.")
    priority = 10
    component = "premise"
    mode_prompt = "mode_premise.txt"
    mode_tools = frozenset({"write_component", "update_scratchpad", "request_review"})
    skeleton = SKEL_PREMISE
    schemas = {"premise": v_premise}
    skeletons = {"premise": SKEL_PREMISE}

    def params(self) -> Dict:
        return {"min_characters": 1, "premise_fields": ["id", "name"]}

    def affected_components(self) -> Tuple[str, ...]:
        return ("premise",)

    def get_errors(self, context) -> List[Error]:
        art = context.artifact
        errs: List[Error] = []

        def build(result, code):
            return checks.as_error(result, type=ErrorType.BUILD, code=code, component="premise")

        for e in (
            build(checks.exists(art, "premise.central_question"), "central_question"),
            build(checks.count(art, "premise.characters", min=context.param("min_characters", 1)),
                  "min_characters"),
            build(checks.each_has(art, "premise.characters",
                                  fields=context.param("premise_fields", ["id", "name"])),
                  "premise_fields"),
        ):
            if e:
                errs.append(e)

        min_endings = context.param("min_endings", 0)
        if min_endings:
            be = build(checks.count(art, "premise.endings", min=min_endings), "min_endings")
            if be:
                errs.append(be)
            de = checks.as_error(checks.distinct(art, "premise.endings", key="id"),
                                 type=ErrorType.FIX, code="distinct_endings", component="premise")
            if de:
                errs.append(de)
        return errs

    def context_view(self, c: Dict) -> Dict:
        return {
            "central_question": c.get("central_question"),
            "characters": [{k: ch[k] for k in _PREMISE_KEEP if ch.get(k) is not None}
                           for ch in c.get("characters", []) if isinstance(ch, dict)],
            "endings": c.get("endings"),
        }

    def render_context(self, ctx: Dict) -> str:
        # Premise is the source every later scene draws from — author it from the bare request, not
        # spec/skeleton noise (the shape rides on the system prompt): title + request + premise to-do.
        spec = ctx.get("spec", {}) or {}
        out = [f"TITLE: {spec.get('title', '')}", "", f"REQUEST: {spec.get('request', '')}"]
        premise_todo = [f"- {e.code}: {e.message}" for e in ctx.get("todo", [])
                        if e.component == "premise"]
        if premise_todo:
            out += ["", "TO-DO (failing done-conditions for premise):", *premise_todo]
        return "\n".join(out)


MODULE = Cast()
register_module(MODULE)
