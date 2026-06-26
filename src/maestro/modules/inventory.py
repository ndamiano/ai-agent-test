"""inventory — the item catalog. Authors the `items` component.

Declares what items exist — each with an id, a name, and examine text. It owns only the catalogue;
whether an item can be obtained and is ever used is the `state` wiring invariant's job, and picking
items up / spending them is done by `world` hotspots and node effects that reference these ids.

Example games:
  - "an escape room of keys, tools, and a final combination" — cast + world + inventory + state
  - "a fetch-quest through a night market"                    — cast + world + inventory + scenes
"""

from typing import Dict, List, Optional

from maestro.modules import checks
from maestro.modules.module import Error, ErrorType, Module, register_module


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

    def params(self) -> Dict:
        return {"min_items": 1}

    def get_errors(self, context) -> List[Error]:
        art = context.artifact

        def build(result, code):
            return checks.as_error(result, type=ErrorType.BUILD, code=code, component="items")

        errs: List[Error] = []
        for e in (
            build(checks.count(art, "items.items", min=context.param("min_items", 1)), "min_items"),
            build(checks.each_has(art, "items.items", fields=["id", "name"]), "item_fields"),
            build(checks.distinct(art, "items.items", key="id"), "distinct_items"),
        ):
            if e:
                errs.append(e)
        return errs


MODULE = Inventory()
register_module(MODULE)
