import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.story_state import init_story_state, apply_delta


def _spec():
    return Spec({"frozen": True, "components": [{"id": "node_scripts", "done_conditions": []}],
                 "story_state_schema": {"entity_states": {"betta": {"trust": "wary"}}}})


# ── story_state merge semantics ──────────────────────────────────────────────

def test_init_from_schema():
    ss = init_story_state({"established_facts": ["world is ending"],
                           "entity_states": {"betta": {"trust": "wary"}}})
    assert ss["established_facts"] == ["world is ending"]
    assert ss["entity_states"]["betta"]["trust"] == "wary"
    assert ss["open_threads"] == [] and ss["recent_events_tail"] == []


def test_apply_delta_merges_all_fields():
    ss = init_story_state()
    apply_delta(ss, {
        "new_facts": ["tablet is fake"],
        "entity_updates": {"betta": {"trust": "warming", "location": "harbor"}},
        "open_threads_add": ["who sent the letter?"],
        "event_summary": "Alex meets Betta",
    })
    assert ss["established_facts"] == ["tablet is fake"]
    assert ss["entity_states"]["betta"] == {"trust": "warming", "location": "harbor"}
    assert ss["open_threads"] == ["who sent the letter?"]
    assert ss["recent_events_tail"] == ["Alex meets Betta"]


def test_apply_delta_entity_key_merge_not_clobber():
    ss = init_story_state({"entity_states": {"betta": {"trust": "wary"}}})
    apply_delta(ss, {"entity_updates": {"betta": {"location": "docks"}}})
    assert ss["entity_states"]["betta"] == {"trust": "wary", "location": "docks"}


def test_facts_and_threads_dedupe_and_resolve():
    ss = init_story_state()
    apply_delta(ss, {"new_facts": ["x"], "open_threads_add": ["t1", "t2"]})
    apply_delta(ss, {"new_facts": ["x"], "open_threads_resolve": ["t1"]})  # x deduped, t1 resolved
    assert ss["established_facts"] == ["x"]
    assert ss["open_threads"] == ["t2"]


def test_recent_events_tail_ages_out():
    ss = init_story_state()
    for i in range(5):
        apply_delta(ss, {"event_summary": f"e{i}"}, tail_len=3)
    assert ss["recent_events_tail"] == ["e2", "e3", "e4"]   # only last 3 kept


# ── write_node fused tool ────────────────────────────────────────────────────

def test_write_node_writes_script_and_state(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)

    tools["write_node"]("scene_001", "label scene_001:\n    return", {
        "new_facts": ["the storm is coming"],
        "entity_updates": {"betta": {"trust": "warming"}},
        "event_summary": "the opening",
    })

    ns = state.read_component("node_scripts")
    assert ns["node_ids"] == ["scene_001"]
    assert "label scene_001" in ns["scripts"]["scene_001"]

    ss = state.read_story_state()
    assert "the storm is coming" in ss["established_facts"]
    assert ss["entity_states"]["betta"]["trust"] == "warming"
    assert ss["recent_events_tail"] == ["the opening"]


def test_write_node_appends_multiple_nodes(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_001", "label scene_001:\n    jump scene_002")
    tools["write_node"]("scene_002", "label scene_002:\n    return")
    ns = state.read_component("node_scripts")
    assert ns["node_ids"] == ["scene_001", "scene_002"]


def test_read_story_state_seeds_from_schema(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["read_story_state"]()
    assert res["ok"] is True
    assert res["story_state"]["entity_states"]["betta"]["trust"] == "wary"
