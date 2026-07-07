"""inventory — the item catalog. Authors the `items` component, DEMAND-DRIVEN.

An item is DECLARED because something already uses it: a take hotspot obtains it, a node effect
adds/removes it, a use/requires gate spends it. inventory does not guess a catalogue up front — it
authors the item wherever a reference demands one (each with an id, name, and examine text), so the
catalogue is a byproduct of real use and `state`'s producer/consumer invariant holds by
construction. Picking items up / spending them is done by `world` hotspots and node effects.

Example games:
  - "an escape room of keys, tools, and a final combination" — cast + world + inventory + state
  - "a fetch-quest through a night market"                    — cast + world + inventory + scenes
"""

from typing import Dict, List, Optional

from maestro import context_render as cr
from maestro.modules import checks, views
from maestro.modules.module import Check, Error, Module, register_module


def items_block(artifact: Dict) -> list:
    """The full item catalogue as prompt context — what a world/state author places, takes,
    and gates on."""
    items = [i for i in (artifact.get("items") or {}).get("items", [])
             if isinstance(i, dict) and i.get("id")]
    if not items:
        return []
    return ["", "ITEMS (the declared catalogue — take/require these EXACT ids):",
            *(f"  {i['id']} — {i.get('name', '')}: {i.get('examine', '')}" for i in items)]


def item_index(artifact: Dict) -> list:
    """Ids + names only, no examine prose — what a crossref/structural fix needs to name a real
    item; the full `items_block` (with examine) is for authoring calls that place or gate items."""
    items = [i for i in (artifact.get("items") or {}).get("items", [])
             if isinstance(i, dict) and i.get("id")]
    if not items:
        return []
    return ["", "ITEMS (these EXACT ids):",
            *(f"  {i['id']} — {i.get('name', '')}" for i in items)]


def v_item_one(it: Dict) -> Optional[str]:
    """Structural gate for ONE item — shared by add_item and v_items."""
    if not isinstance(it, dict):
        return "an item must be a JSON object"
    if not it.get("id"):
        return "an item needs an 'id' (e.g. 'item_key')"
    if not it.get("name"):
        return "an item needs a 'name'"
    return None


def v_items(c: Dict) -> Optional[str]:
    items = c.get("items")
    if not isinstance(items, list) or not items:
        return "items.items must be a non-empty list of item objects"
    for i, it in enumerate(items):
        err = v_item_one(it)
        if err:
            return f"items.items[{i}]: {err}"
    return None


def _referenced_item_ids(artifact: Dict) -> set:
    """Every item id the artifact REFERS to: node add/remove_item effects + menu-choice item gates,
    place take hotspots + use-outcome effects + move/use/requires item gates. The demand signal."""
    refs: set = set()

    def _effs(effs):
        for eff in effs or []:
            if isinstance(eff, dict):
                for k in ("add_item", "remove_item"):
                    if isinstance(eff.get(k), str):
                        refs.add(eff[k])

    _, nodes = views.nodes_of(artifact)
    for n in nodes.values():
        _effs(checks.node_effects(n))
        end = n.get("end", {}) or {}
        if end.get("type") == "menu":
            for ch in end.get("choices", []) or []:
                refs |= views.cond_items(ch.get("requires"))

    for p in ((artifact.get("places") or {}).get("places") or {}).values():
        for h in p.get("interactables", []) if isinstance(p, dict) else []:
            a = h.get("action", {}) or {}
            if a.get("type") == "take" and isinstance(a.get("item"), str):
                refs.add(a["item"])
            _effs(views.action_effects(a))
            for cond in views.action_conditions(a):
                refs |= views.cond_items(cond)
    return refs


def demanded_items(artifact: Dict) -> List[str]:
    """Referenced-but-undeclared item ids — one authoring job each. The catalogue is exactly what
    the game uses: an item appears BECAUSE a hotspot/effect/gate named it."""
    declared = {i.get("id") for i in (artifact.get("items") or {}).get("items", [])
                if isinstance(i, dict)}
    return sorted(iid for iid in _referenced_item_ids(artifact) if iid and iid not in declared)


def _item_usage_block(artifact: Dict, item_id: str) -> list:
    """Where the target item is USED — the demand context the author writes the item to fit."""
    out: List[str] = []
    _, nodes = views.nodes_of(artifact)
    for nid, n in nodes.items():
        for ln in n.get("lines", []) or []:
            for eff in (ln.get("effects") or []) if isinstance(ln, dict) else []:
                if isinstance(eff, dict) and eff.get("add_item") == item_id:
                    out.append(f"  scene {nid} GIVES it to the player — a line reads: "
                               f"\"{(ln.get('text') or '')[:80]}\"")
                elif isinstance(eff, dict) and eff.get("remove_item") == item_id:
                    out.append(f"  scene {nid} SPENDS it")
    for pid, p in ((artifact.get("places") or {}).get("places") or {}).items():
        for h in p.get("interactables", []) if isinstance(p, dict) else []:
            a = h.get("action", {}) or {}
            if a.get("type") == "take" and a.get("item") == item_id:
                out.append(f"  place {pid}: picked up via the '{h.get('label') or h.get('id')}' hotspot")
            elif item_id in views.cond_items(a.get("requires")):
                out.append(f"  place {pid}: the '{h.get('label') or h.get('id')}' hotspot REQUIRES it")
            elif any(item_id in views.cond_items(c) for c in views.action_conditions(a)):
                out.append(f"  place {pid}: a use on '{h.get('label') or h.get('id')}' spends it")
    if not out:
        return []
    return ["", f"WHERE '{item_id}' IS USED (write the item so it fits these):", *out]


_ADD_TOOLS = frozenset({"add_item", "read_component", "request_review"})

SKEL_ITEM_ONE = (
    '// item_id (the tool arg) is the snake_case id, prefixed item_ (use the EXACT id from your\n'
    '// target — it is already referenced). `content` is this ONE item:\n'
    '{\n'
    '  "name": "<Display Name the player sees>",\n'
    '  "examine": "what the player reads on a close look — what it IS and DOES (material, condition,\n'
    '    function), never what it means"\n'
    '}'
)


def _d_demanded_items(chk, m, ctx):
    """One authoring job per referenced-but-undeclared item — the item is born where it's used."""
    return [Error(type=chk.tier, code=chk.code, component="items", path=iid, ref=iid,
                  message=(f"item '{iid}' is referenced (a take hotspot, a use/requires gate, or an "
                           f"add/remove_item effect) but not declared — author it in the catalog "
                           f"with add_item('{iid}', ...)."))
            for iid in demanded_items(ctx.artifact)]


def _has_items(ctx) -> bool:
    """Demand-driven: the catalog is empty (or absent) until something is demanded, so the
    field/distinct safety checks only apply ONCE items exist — otherwise they'd fire on the empty
    catalog with no item to fix."""
    return bool((ctx.artifact.get("items") or {}).get("items"))


class Inventory(Module):
    id = "inventory"
    description = ("A held-item catalogue — keys, tools, objects the player picks up and carries. "
                   "Include for fetch/use puzzles and anything with an inventory.")
    priority = 35
    component = "items"
    mode_prompt = "inventory_add.txt"
    mode_tools = frozenset({"write_component", "read_component", "request_review"})
    skeleton = SKEL_ITEM_ONE
    schemas = {"items": v_items}
    skeletons = {"items": SKEL_ITEM_ONE}

    # Items are DEMAND-DRIVEN: no up-front count floor. A dangling item reference (a hotspot/effect/
    # gate naming an undeclared item) fans into one author job each, and the item is written with its
    # use in context. The field/distinct checks are cheap safety on the items already written.
    checks = [
        Check("item_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "items.items", fields=["id", "name"])) if _has_items(ctx) else [],
            context=cr.ctx_structural),
        Check("distinct_items", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "items.items", key="id")) if _has_items(ctx) else [],
            context=cr.ctx_structural),
        Check("demanded_items", _d_demanded_items, tools=_ADD_TOOLS, when_clean=True,
              prompt="inventory_add.txt", skeleton=SKEL_ITEM_ONE),
    ]

    def render_context(self, ctx: Dict) -> str:
        # The item is authored FROM its demand: the premise for flavour + where it's used (the
        # consuming site) + the items already declared (so it stays distinct). Never full cast cards
        # or a story dump — an item's name/examine needs the USE, not the whole world.
        art = ctx.get("artifact") or {}
        target = ctx.get("target")
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        if target is not None and getattr(target, "ref", None):
            lines += _item_usage_block(art, target.ref)
        lines += item_index(art)
        lines += cr.tail_block(ctx)
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> list:
        return item_index(artifact)


MODULE = Inventory()
register_module(MODULE)
