"""rewrite_node — regenerate ONE node from a human note, outside the build loop (the per-scene
"rewrite" control). A focused mini-loop: the scene author prompt + a forced write_node (so a
locked nodes component is overwritten), a couple of retries if the model fumbles the tool call.
"""

import json
import logging
from typing import Callable, Dict, Optional

from maestro.services import filter_schemas, parse_action
from maestro.modules.module import load_prompt, skeleton_guide
from maestro.modules.scenes import SKEL_NODES

logger = logging.getLogger(__name__)
_BUILD_MAX_TOKENS = 8000


def rewrite_node(spec: Dict, state, node_id: str, note: str, tools: Dict[str, Callable],
                 connector=None, report: Optional[Callable] = None, cap: int = 4) -> Dict:
    from llm_clients.connector_selector import get_connector
    from llm_clients.message_builder import MessageBuilder
    conn = connector or get_connector()
    ns = state.read_component("nodes") or {}
    existing = (ns.get("nodes") or {}).get(node_id)
    if existing is None:
        return {"ok": False, "error": f"no node {node_id!r} to rewrite"}

    say = report or (lambda _msg: None)
    system = load_prompt("nodes_write.txt") + f"\n\n{skeleton_guide('nodes', SKEL_NODES)}"
    schemas = filter_schemas({"write_node"})
    art = state.load_artifact()

    # The scene author's crafted context (cards/locations/story/items), same as the build loop —
    # never a raw dump of every component (that overflowed the window on a live build).
    from maestro.modules import assets, cast, inventory, story
    task = "\n".join([
        f"Rewrite the dialogue node '{node_id}'. Keep this exact node id.",
        f"Unless the direction says otherwise, keep its `end` "
        f"({json.dumps(existing.get('end', {}), ensure_ascii=False)}) so it stays wired in.",
        "",
        f"HUMAN DIRECTION (the change to make): {note}",
        "",
        f"CURRENT NODE:\n{json.dumps(existing, ensure_ascii=False)}",
        *cast.character_cards(art),
        *assets.locations_block(art),
        *story.story_block(art),
        *inventory.items_block(art),
        f"STORY STATE: {json.dumps(state.read_story_state() or {}, ensure_ascii=False)}",
        "",
        f"Call write_node with node_id='{node_id}' and the full rewritten content. Tool call only.",
    ])
    mb = MessageBuilder(system).add_user(task)

    for _ in range(cap):
        action = parse_action(conn.generate_with_tools(mb.build(), schemas, reasoning="high",
                                                        max_tokens=_BUILD_MAX_TOKENS), schemas)
        if action.get("tool") != "write_node":
            say("no write_node tool call — nudging")
            mb.add_user("Respond with a write_node TOOL CALL (not prose), passing the rewritten "
                        f"content for node_id='{node_id}'.")
            continue
        args = {**(action.get("args") or {}), "node_id": node_id, "force": True}
        result = tools["write_node"](**args)
        if result.get("ok"):
            say(f"rewrote {node_id}")
            return {"ok": True, "node_id": node_id}
        say(f"write_node rejected: {result.get('error')}")
        mb.add_user(f"That was rejected: {result.get('error')}. Fix it and call write_node again.")
    return {"ok": False, "error": f"could not rewrite {node_id!r} after {cap} attempts"}
