"""Story-state object — the continuity bible, distinct from the script output.

Content/story state ("what's happened in the game world") is cumulative and
order-dependent — it cannot be recomputed from the artifact, only carried. So every
node-writing step produces TWO things: the node content AND a delta to this object.
The next node reads this snapshot, NOT the prior script text — which keeps context
~constant as the script grows.

It's a current-state snapshot, not a log, structured around what continuity breaks
on (not lossy prose):
  established_facts   — durable truths the player has learned
  entity_states       — per-entity current state (trust, location, ...)
  open_threads        — raised-but-unresolved subplots
  recent_events_tail  — the last few events in fuller detail; older ones age out
                        (the agent promotes anything durable into established_facts)

Spine-tracked: a single cumulative snapshot, with branches as local variations that
rejoin. Per-path precondition/postcondition rigor is a later refinement.
"""

from typing import Dict, List

_TAIL_LEN = 3


def init_story_state(schema: Dict = None) -> Dict:
    """Seed an empty story state, honoring any fields the spec declared."""
    schema = schema or {}
    return {
        "established_facts": list(schema.get("established_facts", [])),
        "entity_states": dict(schema.get("entity_states", {})),
        "open_threads": list(schema.get("open_threads", [])),
        "recent_events_tail": [],
    }


def _dedupe_extend(base: List, items) -> None:
    for it in items or []:
        if it not in base:
            base.append(it)


def apply_delta(state: Dict, delta: Dict, tail_len: int = _TAIL_LEN) -> Dict:
    """Merge a node's story-state delta into the snapshot (in place) and return it.

    delta fields (all optional):
      new_facts:            [str]            appended to established_facts (deduped)
      entity_updates:       {entity: {...}}  per-entity key merge
      open_threads_add:     [str]            appended (deduped)
      open_threads_resolve: [str]            removed from open_threads
      event_summary:        str              appended to the recent tail
    """
    for key in ("established_facts", "entity_states", "open_threads", "recent_events_tail"):
        state.setdefault(key, [] if key != "entity_states" else {})

    _dedupe_extend(state["established_facts"], delta.get("new_facts"))

    # Models sometimes send entity_updates as a LIST ([{entity, ...updates}] or plain strings)
    # instead of a map — normalize instead of crashing the whole scene write.
    eu = delta.get("entity_updates")
    if isinstance(eu, list):
        eu = {e.pop("entity"): e for e in eu
              if isinstance(e, dict) and isinstance(e.get("entity"), str)}
    for entity, updates in (eu or {} if isinstance(eu, (dict, type(None))) else {}).items():
        cur = state["entity_states"].get(entity)
        if isinstance(cur, dict) and isinstance(updates, dict):
            cur.update(updates)
        else:
            state["entity_states"][entity] = updates

    _dedupe_extend(state["open_threads"], delta.get("open_threads_add"))
    for thread in delta.get("open_threads_resolve") or []:
        if thread in state["open_threads"]:
            state["open_threads"].remove(thread)

    summary = delta.get("event_summary")
    if summary:
        tail = state["recent_events_tail"]
        tail.append(summary)
        # Age out: anything beyond the tail window drops off. The agent is expected
        # to have promoted durable consequences into new_facts.
        del tail[:-tail_len]

    return state
