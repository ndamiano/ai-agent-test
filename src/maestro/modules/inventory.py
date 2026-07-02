"""inventory — the item catalog. Authors the `items` component.

Declares what items exist — each with an id, a name, and examine text. It owns only the catalogue;
whether an item can be obtained and is ever used is the `state` wiring invariant's job, and picking
items up / spending them is done by `world` hotspots and node effects that reference these ids.

Example games:
  - "an escape room of keys, tools, and a final combination" — cast + world + inventory + state
  - "a fetch-quest through a night market"                    — cast + world + inventory + scenes
"""

from typing import Dict, Optional

from maestro.modules import checks
from maestro.modules.module import Check, Module, register_module


def items_block(artifact: Dict) -> list:
    """The full item catalogue as prompt context — what a world/state author places, takes,
    and gates on."""
    items = [i for i in (artifact.get("items") or {}).get("items", [])
             if isinstance(i, dict) and i.get("id")]
    if not items:
        return []
    return ["", "ITEMS (the declared catalogue — take/require these EXACT ids):",
            *(f"  {i['id']} — {i.get('name', '')}: {i.get('examine', '')}" for i in items)]


def v_items(c: Dict) -> Optional[str]:
    items = c.get("items")
    if not isinstance(items, list) or not items:
        return "items.items must be a non-empty list of item objects"
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return f"items.items[{i}] must be an object"
        if not it.get("id"):
            return f"items.items[{i}] needs an 'id' (e.g. 'item_key')"
        if not it.get("name"):
            return f"items.items[{i}] needs a 'name'"
    return None


SKEL_ITEMS = (
    '{\n'
    '  "items": [\n'
    '    {"id": "item_<thing>", "name": "<Display Name>", "examine": "what the player reads on look"}\n'
    '  ]\n'
    '}\n'
    '// An ITEM is a thing the player can hold. id is snake_case, prefixed item_.\n'
    '// The catalogue only DECLARES items; a `world` take hotspot (or a node add_item effect) is how\n'
    '//   one is obtained, and a `requires` on the item is how it is used.'
)


class Inventory(Module):
    id = "inventory"
    description = ("A held-item catalogue — keys, tools, objects the player picks up and carries. "
                   "Include for fetch/use puzzles and anything with an inventory.")
    priority = 35
    component = "items"
    mode_prompt = "inventory_write.txt"
    mode_tools = frozenset({"write_component", "update_scratchpad", "request_review"})
    skeleton = SKEL_ITEMS
    schemas = {"items": v_items}
    skeletons = {"items": SKEL_ITEMS}

    checks = [
        Check("min_items", lambda chk, m, ctx: m.wrap(chk, checks.count(
            ctx.artifact, "items.items", min=ctx.param("min_items", 1)))),
        Check("item_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "items.items", fields=["id", "name"]))),
        Check("distinct_items", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "items.items", key="id"))),
    ]

    def params(self) -> Dict:
        return {"min_items": 1}

    def render_context(self, ctx: Dict) -> str:
        # Items serve the story: the catalogue is invented against the plan (question/endings)
        # and the cast, so every item can matter to someone's want.
        from maestro import context_render as cr
        from maestro.modules import cast, story
        art = ctx.get("artifact") or {}
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += story.story_block(art)
        lines += cast.character_cards(art)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)


MODULE = Inventory()
register_module(MODULE)
