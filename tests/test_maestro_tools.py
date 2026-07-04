import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools, SpecNotFrozen


def _spec(frozen=True):
    # Unconstrained: no modules => no structural schemas / locks, so these tests exercise the tool
    # mechanics (frozen gate, persistence, edits) without per-component validation getting in the way.
    return Spec({"title": "T", "frozen": frozen, "modules": [], "params": {}})


def _node(text="hi", end=None):
    return {"lines": [{"speaker": "a", "text": text}], "end": end or {"type": "return"}}


# ── add_interactable: append a hotspot without clobbering siblings ────────────

def _places_spec():
    return Spec({"title": "T", "frozen": True, "modules": [], "params": {}})


def _seed_place(state):
    state.write_component("places", {"place_ids": ["r1"], "places": {"r1": {
        "kind": "room", "background": "bg",
        "interactables": [{"id": "h_a", "action": {"type": "examine", "text": "x"}}]}}})


def test_add_interactable_appends_preserving_siblings(tmp_path):
    state = RunState(tmp_path)
    _seed_place(state)
    tools = build_tools(_places_spec(), state)
    res = tools["add_interactable"]("r1", {"id": "h_to_r2", "label": "exit",
        "position": {"rect": {"x": 1, "y": 1, "w": 1, "h": 1}},
        "action": {"type": "move", "target": "r2"}})
    assert res["ok"] is True
    ids = [i["id"] for i in state.read_component("places")["places"]["r1"]["interactables"]]
    assert ids == ["h_a", "h_to_r2"]                      # sibling preserved, new one appended


def test_add_interactable_rejects_dupe_and_bad_shape(tmp_path):
    state = RunState(tmp_path)
    _seed_place(state)
    tools = build_tools(_places_spec(), state)
    assert tools["add_interactable"]("r1", {"id": "h_a", "action": {"type": "examine", "text": "y"}})["ok"] is False
    assert tools["add_interactable"]("nope", {"id": "h_x", "action": {"type": "examine", "text": "y"}})["ok"] is False
    assert tools["add_interactable"]("r1", {"id": "h_y"})["ok"] is False   # action missing


# ── write-time action validation: catch malformed IR at the tool call ────────

def test_action_validation_rejects_empty_requires_with_hint():
    from maestro.modules.world import action_error as _action_struct_error
    err = _action_struct_error({"type": "use", "clauses": [
        {"requires": {}, "outcome": {"text": "pray", "effects": [{"set_flag": "f"}]}}]})
    assert err and "requires" in err and "fallback" in err   # actionable: use fallback instead


def test_action_validation_names_the_bad_key():
    # 'place_id' instead of 'target' burned 26 live-build steps against the oneOf validator's
    # nameless rejection — the message must say which key is wrong and what the shape is.
    from maestro.modules.world import action_error as _action_struct_error
    msg = _action_struct_error({"type": "move", "place_id": "z2",
                                "spawn": {"cell": {"x": 1, "y": 1}}})
    assert "place_id" in msg and "target" in msg
    msg = _action_struct_error({"type": "take"})
    assert "item" in msg
    msg = _action_struct_error({"type": "teleport"})
    assert "teleport" in msg and "move" in msg


def test_action_validation_allows_unconditional_fallback_use():
    from maestro.modules.world import action_error as _action_struct_error
    # The unconditional pattern the model wanted (always set a flag) is fallback-only, no clauses.
    assert _action_struct_error({"type": "use",
        "fallback": {"text": "pray", "effects": [{"set_flag": "f"}]}}) is None
    assert _action_struct_error({"type": "use"}) is not None   # neither clauses nor fallback


def test_add_interactable_rejects_invalid_action(tmp_path):
    state = RunState(tmp_path)
    _seed_place(state)
    tools = build_tools(_places_spec(), state)
    bad = {"id": "h_bad", "position": {"rect": {"x": 1, "y": 1, "w": 1, "h": 1}},
           "action": {"type": "use", "clauses": [{"requires": {}, "outcome": {"text": "x"}}]}}
    res = tools["add_interactable"]("r1", bad)
    assert res["ok"] is False and "requires" in res["error"]


# ── generic component / state tools ──────────────────────────────────────────

def test_write_component_refuses_when_unfrozen(tmp_path):
    tools = build_tools(_spec(frozen=False), RunState(tmp_path))
    with pytest.raises(SpecNotFrozen):
        tools["write_component"]("premise", {"x": 1})


def test_write_component_refuses_unknown_id(tmp_path):
    # A model wrote module-name "inventory" for the `items` component and got an "ok" for a junk
    # file — a false success it looped on. Unknown ids must fail and list the real components.
    state = RunState(tmp_path)
    tools = build_tools({"title": "T", "frozen": True,
                         "modules": ["cast", "world", "inventory"], "params": {}}, state)
    res = tools["write_component"]("inventory", {"items": [{"id": "item_x", "name": "X"}]})
    assert res["ok"] is False and "'items'" in res["error"]
    assert state.read_component("inventory") is None


def test_write_and_read_component(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_component"]("premise", {"central_question": "Q?"})["ok"] is True
    got = tools["read_component"]("premise")
    assert got["ok"] is True and got["content"]["central_question"] == "Q?"
    assert tools["read_component"]("missing")["ok"] is False


def test_validate_tool_reports_failures(tmp_path):
    from maestro.modules import compose
    spec = {"frozen": True, "modules": ["cast"], "params": {}}
    tools = build_tools(spec, RunState(tmp_path), compose(("cast",)))
    assert tools["validate"]()["ok"] is False        # empty cast -> cast reports errors
    tools["write_component"]("characters",
                             {"characters": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
    assert tools["validate"]()["ok"] is True


def test_no_manual_compile_tool(tmp_path):
    # The executor runs `compiles` every step; a manual compile tool only wastes steps and lets
    # the agent compile early, fighting the "compiles last" ordering — so it must not exist.
    tools = build_tools(_spec(), RunState(tmp_path))
    assert "compile_renpy" not in tools and "compile" not in tools


def test_generate_asset_wraps_image_gen(tmp_path, monkeypatch):
    import renpy.fns as fns
    monkeypatch.setattr(fns, "generate_images", lambda inputs, wd: {"status": "ok", "generated": ["bg_x.png"]})
    tools = build_tools(_spec(), RunState(tmp_path))
    res = tools["generate_asset"]()
    assert res["ok"] is True and res["generated"] == ["bg_x.png"]


def test_generate_asset_refuses_when_unfrozen(tmp_path):
    tools = build_tools(_spec(frozen=False), RunState(tmp_path))
    with pytest.raises(SpecNotFrozen):
        tools["generate_asset"]()


def test_update_scratchpad(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["update_scratchpad"](current_goal="write premise", open_questions=["which tone?"])
    pad = state.read_scratchpad()
    assert pad["current_goal"] == "write premise"
    assert pad["open_questions"] == ["which tone?"]


def test_request_review_returns_pending(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    res = tools["request_review"]("Romance or tragedy?", ["romance", "tragedy"])
    assert res["status"] == "review_requested" and res["options"] == ["romance", "tragedy"]


# ── structured node tools ────────────────────────────────────────────────────

def test_write_node_persists_ir_object(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["write_node"]("scene_01", _node("hello", {"type": "jump", "target": "scene_02"}))
    assert res["ok"] is True
    ns = state.read_component("nodes")
    assert ns["node_ids"] == ["scene_01"]
    assert ns["nodes"]["scene_01"]["lines"][0]["text"] == "hello"
    assert ns["nodes"]["scene_01"]["end"] == {"type": "jump", "target": "scene_02"}


def test_write_node_stamps_system_beat(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", _node("hi", {"type": "jump", "target": "scene_02"}),
                        beat="beat_03")
    assert state.read_component("nodes")["nodes"]["scene_01"]["beat"] == "beat_03"


def test_write_node_rejects_bad_shape(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_node"]("s1", "label s1:\n  a \"hi\"")["ok"] is False   # string, not object
    assert tools["write_node"]("s1", {"lines": [], "end": {"type": "return"}})["ok"] is False  # empty
    assert tools["write_node"]("s1", {"lines": [{"text": "x"}]})["ok"] is False  # no end
    assert tools["write_node"]("s1", {"lines": [{"text": "x"}], "end": {"type": "boom"}})["ok"] is False


def test_write_node_rejects_start_id(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_node"]("start", _node())["ok"] is False


def test_write_node_merges_story_state_delta(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", _node(), new_facts=["a clue surfaced"],
                        entity_updates={"mara": {"trust": "wary"}})
    ss = state.read_story_state()
    assert "a clue surfaced" in ss["established_facts"]
    assert ss["entity_states"]["mara"]["trust"] == "wary"


def test_write_node_persists_event_summary_as_synopsis(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", _node(), event_summary="sisters find the ledger")
    assert state.read_component("nodes")["synopses"]["scene_01"] == "sisters find the ledger"


def test_write_node_tolerates_malformed_delta(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_node"]("s1", _node(), story_state_delta=["new_facts"])["ok"] is True
    assert tools["write_node"]("s2", _node(), story_state_delta="oops")["ok"] is True


def test_edit_node_patches_line_and_end(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", {"lines": [{"speaker": "a", "text": "old"}],
                               "end": {"type": "jump", "target": "s2"}})
    assert tools["edit_node"]("s1", line_index=0, text="new", speaker="b")["ok"] is True
    assert tools["edit_node"]("s1", end={"type": "return"})["ok"] is True
    node = state.read_component("nodes")["nodes"]["s1"]
    assert node["lines"][0] == {"speaker": "b", "text": "new"}
    assert node["end"] == {"type": "return"}


def test_edit_node_errors(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node())
    assert tools["edit_node"]("missing", end={"type": "return"})["ok"] is False
    assert tools["edit_node"]("s1", line_index=9, text="x")["ok"] is False
    assert tools["edit_node"]("s1", end={"type": "boom"})["ok"] is False


def test_edit_node_end_patch_rejects_fake_menu(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node())
    fake = {"type": "menu", "choices": [{"text": "a", "target": "s2"},
                                        {"text": "b", "target": "s2"}]}
    res = tools["edit_node"]("s1", end=fake)
    assert res["ok"] is False and "fake choice" in res["error"]
    real = {"type": "menu", "choices": [{"text": "a", "target": "s2"},
                                        {"text": "b", "target": "s3"}]}
    assert tools["edit_node"]("s1", end=real)["ok"] is True


def test_edit_node_full_replace_preserves_beat(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node(), beat="beat_01")
    assert state.read_component("nodes")["nodes"]["s1"]["beat"] == "beat_01"
    replacement = {"lines": [{"speaker": "a", "text": "new"}], "end": {"type": "return"}}
    assert tools["edit_node"]("s1", content=replacement)["ok"] is True
    assert state.read_component("nodes")["nodes"]["s1"]["beat"] == "beat_01"


def test_write_node_overwrite_preserves_beat(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node(), beat="beat_01")
    tools["write_node"]("s1", {"lines": [{"speaker": "a", "text": "rewritten"}],
                               "end": {"type": "return"}})
    assert state.read_component("nodes")["nodes"]["s1"]["beat"] == "beat_01"


def test_menu_choice_requires_target(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    bad = {"lines": [{"speaker": "a", "text": "x"}],
           "end": {"type": "menu", "choices": [{"text": "a", "target": "s2"}, {"text": "b"}]}}
    res = tools["write_node"]("s1", bad)
    assert res["ok"] is False and "target" in res["error"]


def test_effects_must_be_objects(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    bad = {"lines": [{"speaker": "a", "text": "x", "effects": ["set_flag"]}],
           "end": {"type": "return"}}
    res = tools["write_node"]("s1", bad)
    assert res["ok"] is False and "effect OBJECT" in res["error"]


def test_write_component_nodes_preserves_beats_and_synopses(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node(), beat="beat_01", event_summary="the reveal")
    rewrite = {"node_ids": ["s1"], "nodes": {"s1": {
        "lines": [{"speaker": "a", "text": "new"}], "end": {"type": "return"}}}}
    assert tools["write_component"]("nodes", rewrite, force=True)["ok"] is True
    ns = state.read_component("nodes")
    assert ns["nodes"]["s1"]["beat"] == "beat_01"
    assert ns["synopses"]["s1"] == "the reveal"
    fake = {"node_ids": ["s1"], "nodes": {"s1": {
        "lines": [{"speaker": "a", "text": "x"}],
        "end": {"type": "menu", "choices": [{"text": "a", "target": "z"},
                                            {"text": "b", "target": "z"}]}}}}
    res = tools["write_component"]("nodes", fake, force=True)
    assert res["ok"] is False and "fake choice" in res["error"]


def test_edit_node_rejects_empty_text(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node())
    res = tools["edit_node"]("s1", line_index=0, text="  ")
    assert res["ok"] is False and "empty" in res["error"]


def test_human_todo_marked_applied_after_fix(tmp_path):
    from maestro.modules import human as human_mod
    from maestro.modules import compose
    from maestro.modules.context import build_context

    state = RunState(tmp_path)
    spec = Spec({"title": "T", "frozen": True, "modules": ["scenes"], "params": {}})
    tools = build_tools(spec, state)
    tools["write_node"]("s1", _node())
    todo = human_mod.add_todo(state, "nodes", "make s1 angrier")
    human = [m for m in compose(("scenes",)) if m.id == "human"][0]
    ctx = build_context(spec.data, state)
    err = next(e for e in human.get_errors(ctx) if e.code == "human_todo")

    class StubServices:
        def __init__(self):
            self.state = state

        def run(self, prompt, *, dispatch=None):
            dispatch("edit_node", {"node_id": "s1", "line_index": 0, "text": "angrier line"})

    def dispatch(name, args):
        return tools[name](**(args or {}))

    human_mod._run_todo_fix(human, ctx, err, 0, StubServices(), dispatch)
    assert human_mod.open_todos(state) == []


def test_crossref_crash_is_surfaced_not_swallowed():
    from maestro.modules.checks import crossref_failures
    art = {"characters": {"characters": [{"id": "a", "name": "A"}]},
           "asset_manifest": {"backgrounds": [], "characters": []},
           "nodes": {"node_ids": ["s1"], "nodes": {"s1": {
               "lines": [{"speaker": "a", "text": "x", "effects": [{"set_var": "gold"}]}],
               "end": {"type": "return"}}}}}
    recs = crossref_failures(art)
    assert recs and "malformed" in recs[0]["message"]


def test_pick_voice_respects_sex_and_override():
    from tools.tts_tools import pick_voice
    bank = ["af_heart", "af_bella", "bf_emma", "am_michael", "am_puck", "bm_george"]
    # a male character never hashes onto a female voice, whatever the id
    for cid in ("danny", "marc", "x", "abcdef"):
        assert pick_voice(cid, bank, sex="male") in {"am_michael", "am_puck", "bm_george"}
        assert pick_voice(cid, bank, sex="female") in {"af_heart", "af_bella", "bf_emma"}
    assert pick_voice("danny", bank, sex="male", tts_voice="am_puck") == "am_puck"
    # deterministic across calls
    assert pick_voice("danny", bank, sex="male") == pick_voice("danny", bank, sex="male")
    # non-kokoro bank (no prefix convention) falls back to whole bank
    assert pick_voice("danny", ["speaker1", "speaker2"], sex="male") in {"speaker1", "speaker2"}


def test_parse_turn():
    from maestro.modules.scenes import _parse_turn
    me = {"id": "elara", "name": "Elara"}
    lines = _parse_turn('NARR: She lifts the lid.\n"Hand me the tape."\n[END]', me)
    assert lines == [{"speaker": None, "text": "She lifts the lid."},
                     {"speaker": "elara", "text": "Hand me the tape."}]
    # strips a self-name prefix the model sometimes adds
    assert _parse_turn("ELARA: Put it down.", me) == [{"speaker": "elara", "text": "Put it down."}]
    # a line the agent writes FOR its scene partner is dropped, never re-attributed
    lines = _parse_turn("juniper: Take the money.\nI won't repeat myself.", me,
                        others={"juniper", "Juniper"})
    assert lines == [{"speaker": "elara", "text": "I won't repeat myself."}]
    # a bare self-name glued on without a colon is stripped
    assert _parse_turn("Elara You had me worried there.", me) == \
        [{"speaker": "elara", "text": "You had me worried there."}]
    # third-person narration about the partner, emitted as speech, becomes narration
    lines = _parse_turn("Juniper's breath hitches in her throat. She slumps back.", me,
                        others={"juniper", "Juniper"})
    assert lines == [{"speaker": None,
                      "text": "Juniper's breath hitches in her throat. She slumps back."}]
    # ...but a vocative to the partner stays speech (it carries a you/I)
    lines = _parse_turn("Juniper you can't just pause reality.", me,
                        others={"juniper", "Juniper"})
    assert lines == [{"speaker": "elara", "text": "Juniper you can't just pause reality."}]
    # a garbled snake_case speaker tag (misspelled cast id) is dropped, never re-attributed
    assert _parse_turn("junyper_vance: Take the money.", me, others={"juniper_vance"}) == []
    # mechanics parentheticals are stripped from play text
    assert _parse_turn("I'm allowing you to board. (flag: loophole_found)", me) == \
        [{"speaker": "elara", "text": "I'm allowing you to board."}]
    # RP-prose habits: pronoun narration becomes NARR; attributed quotes keep only the speech
    lines = _parse_turn("He lowers the controller. His hands are shaking.", me)
    assert lines == [{"speaker": None,
                      "text": "He lowers the controller. His hands are shaking."}]
    lines = _parse_turn("'We got it,' he says, voice cracking.", me)
    assert lines == [{"speaker": "elara", "text": "We got it"}]
    # a licensed ramble splits at sentence ends into breath-sized lines, text preserved
    long = ("I checked the manifest twice and the numbers do not add up at all. " * 4).strip()
    lines = _parse_turn(long, me)
    assert len(lines) > 1
    assert " ".join(ln["text"] for ln in lines) == long
    assert all(len(ln["text"].split()) <= 55 for ln in lines)


def test_scene_turn_loop_writes_via_guarded_dispatch(tmp_path):
    from maestro.modules import compose
    from maestro.modules.context import build_context
    from maestro.modules.scenes import scene_turn_loop

    state = RunState(tmp_path)
    state.write_component("characters", {"characters": [
        {"id": "a", "name": "Ada", "drive": "leave"}, {"id": "b", "name": "Bo", "drive": "stay"}]})
    state.write_component("story", {"central_question": "q",
                                    "endings": [{"id": "ending_x", "description": "d"}],
                                    "beats": [{"id": "beat_1", "summary": "s1"},
                                              {"id": "beat_2", "summary": "s2"}]})
    # a pre-existing scene seeds the cross-scene dedupe: its LONG lines' openers are spent,
    # its short lines stay available for deadpan callbacks
    state.write_component("nodes", {"node_ids": ["intro"], "nodes": {"intro": {
        "beat": "beat_0",
        "lines": [{"speaker": "a",
                   "text": "The spawn rate on the final platform is inconsistent today."},
                  {"speaker": "b", "text": "We have time."}],
        "end": {"type": "jump", "target": "scene_01"}}}})
    spec = Spec({"title": "T", "frozen": True, "modules": ["scenes"],
                 "params": {"each_node_min_lines": 3}})
    scenes = [m for m in compose(("scenes",)) if m.id == "scenes"][0]
    ctx = build_context(spec.data, state)
    err = next(e for e in scenes.get_errors(ctx) if e.code == "beats_realized")

    # echo policy: a short line may recur once (deadpan echo), a third occurrence is blocked;
    # a long line repeating its 4-word opener is circling — in-scene AND across scenes
    turn_replies = iter([
        "Pack the crate.",
        "NARR: Bo blocks the door.\nNot that one.",
        "Pack the crate.",
        "Pack the crate.",
        "We have time.",
        "The spawn rate on the second platform is worse.",
        "You're treating this crate like it's the last one in the world.",
        "You're treating this crate like a problem we can fix tonight.",
        "The list is in your pocket.",
        "Take it. [END]"])

    class StubServices:
        def __init__(self, tools):
            self.tools, self.state = tools, state
            self.reports = []
            self.turn_msgs = []

        def infer(self, msgs, schemas, **kw):
            if schemas:  # the closer call
                import json as j
                return {"choices": [{"message": {"tool_calls": [{"function": {
                    "name": "finish_scene", "arguments": j.dumps({
                        "end": {"type": "jump", "target": "scene_beat_2"},
                        "event_summary": "crate fight"})}}]}}]}
            self.turn_msgs.append(msgs)
            return {"choices": [{"message": {"content": next(turn_replies)}}]}

        def dispatch(self, name, args):
            return self.tools[name](**(args or {}))

        def _report(self, s):
            self.reports.append(s)

    tools = build_tools(spec, state, [scenes])
    svc = StubServices(tools)
    scene_turn_loop(scenes, ctx, err, 0, svc, svc.dispatch)

    nodes = state.read_component("nodes")
    assert nodes["node_ids"] == ["intro", "scene_01"]
    node = nodes["nodes"]["scene_01"]
    assert node["end"] == {"type": "jump", "target": "scene_beat_2"}
    assert {"speaker": None, "text": "Bo blocks the door."} in node["lines"]
    speakers = [ln["speaker"] for ln in node["lines"]]
    assert "a" in speakers and "b" in speakers
    texts = [ln["text"] for ln in node["lines"]]
    assert texts.count("Pack the crate.") == 2
    assert sum(1 for t in texts if t.startswith("You're treating this crate")) == 1
    # cross-scene: intro's long opener is spent; its short line is a legal callback
    assert not any(t.startswith("The spawn rate on") for t in texts)
    assert "We have time." in texts
    assert state.read_component("nodes")["synopses"]["scene_01"] == "crate fight"
    # the last rounds before the turn cap carry the close-the-scene nudge; early ones don't
    assert "[END]" not in svc.turn_msgs[0][-1]["content"]
    assert "Bring the scene to a close" in svc.turn_msgs[-1][-1]["content"]


def test_parse_args_normalizes_model_shapes():
    from maestro.services import parse_args
    assert parse_args('{"a": 1}') == {"a": 1}
    assert parse_args({"a": 1}) == {"a": 1}                       # already-parsed dict
    assert parse_args({'{"a": 1}': ""}) == {"a": 1}               # JSON blob as the only KEY
    assert parse_args('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_args("[1, 2]") == {}                             # non-dict JSON
    assert parse_args("not json") == {}


def test_edit_node_coerces_wrapped_speaker(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    state.write_component("nodes", {"node_ids": ["n1"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "x"}], "end": {"type": "end"}}}})
    assert tools["edit_node"]("n1", line_index=0, speaker={"id": "b"})["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"]["lines"][0]["speaker"] == "b"
    assert tools["edit_node"]("n1", line_index=0, speaker=["c"])["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"]["lines"][0]["speaker"] == "c"
    assert tools["edit_node"]("n1", line_index=0, speaker={"x": 1})["ok"] is False


def test_reachable_from_start_vacuous_on_empty_graph():
    from maestro.modules.scenes import reachable_from_start
    ok, _ = reachable_from_start({})
    assert ok is True
    ok, _ = reachable_from_start({"nodes": {"node_ids": [], "nodes": {}}})
    assert ok is True


def test_force_ending_end_is_mechanical(tmp_path):
    from maestro.modules.context import build_context
    from maestro.modules import compose
    from maestro.modules.scenes import _force_ending_end

    state = RunState(tmp_path)
    state.write_component("story", {"central_question": "q",
                                    "endings": [{"id": "ending_x", "description": "d"}],
                                    "beats": [{"id": "b1", "summary": "s"}]})
    state.write_component("nodes", {"node_ids": ["ending_x"], "nodes": {
        "ending_x": {"lines": [{"speaker": None, "text": "t"}],
                     "end": {"type": "jump", "target": "ending_x_final"}}}})
    spec = Spec({"title": "T", "frozen": True, "modules": ["scenes"], "params": {}})
    scenes_mod = [m for m in compose(("scenes",)) if m.id == "scenes"][0]
    ctx = build_context(spec.data, state)
    err = next(e for e in scenes_mod.get_errors(ctx) if e.code == "ending_nodes_end")
    tools = build_tools(spec, state)

    class Svc:
        allowed = frozenset({"read_node"})

        def __init__(self):
            self.reports = []

        def dispatch(self, name, args):
            return tools[name](**(args or {}))

        def _report(self, s):
            self.reports.append(s)

        def run(self, *a, **k):
            raise AssertionError("forcing a planned ending's end must never call the LLM")

    svc = Svc()
    _force_ending_end(scenes_mod, ctx, err, 0, svc, svc.dispatch)
    assert state.read_component("nodes")["nodes"]["ending_x"]["end"] == {"type": "end"}


def test_state_bare_declaration_cut_is_mechanical(tmp_path):
    from maestro.modules.context import build_context
    from maestro.modules.state import MODULE as st, _cut_bare_declaration

    state = RunState(tmp_path)
    state.write_component("items", {"items": [
        {"id": "item_dead", "name": "Dead", "examine": "x"},
        {"id": "item_live", "name": "Live", "examine": "y"}]})
    state.write_component("nodes", {"node_ids": ["n1", "n2"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "x", "effects": [{"add_item": "item_live"}]}],
               "end": {"type": "jump", "target": "n2"}},
        "n2": {"lines": [{"speaker": "a", "text": "y"}], "end": {"type": "menu", "choices": [
            {"text": "go", "target": "n1", "requires": {"item": "item_live"}},
            {"text": "stay", "target": "n1"}]}}}})
    spec = Spec({"title": "T", "frozen": True, "modules": ["scenes", "inventory"], "params": {}})
    ctx = build_context(spec.data, state)
    err = next(e for e in st.get_errors(ctx)
               if e.code == "state_wiring" and e.ref == "item_dead")

    tools = build_tools(spec, state)

    class Svc:
        allowed = frozenset({"edit_node"})   # stale scope from a previous step

        def __init__(self):
            self.reports = []

        def dispatch(self, name, args):
            return tools[name](**(args or {}))

        def _report(self, s):
            self.reports.append(s)

        def run(self, *a, **k):
            raise AssertionError("a bare-declaration cut must never call the LLM")

    svc = Svc()
    _cut_bare_declaration(st, ctx, err, 0, svc, svc.dispatch)
    assert [i["id"] for i in state.read_component("items")["items"]] == ["item_live"]
    assert svc.allowed is None


def test_render_beat_carries_type_and_stake():
    from maestro.modules.story import render_beat
    assert render_beat({"id": "beat_01", "summary": "pizza order", "type": "comedy",
                        "tension": "none"}) == "beat_01 — pizza order (comedy, stake: none)"
    assert render_beat({"id": "beat_02", "summary": "the fight"}) == "beat_02 — the fight"


def test_parse_screenplay():
    from maestro.modules.scenes import parse_screenplay
    chars = [{"id": "mara", "name": "Mara"}, {"id": "jonas", "name": "Jonas"}]
    script = ("NARR: The hallway light flickers.\n"
              "MARA [worried]: You left the door open.\n"
              "Jonas: I left it open\n"
              "for you.\n")
    lines, err = parse_screenplay(script, chars)
    assert err is None
    assert lines[0] == {"speaker": None, "text": "The hallway light flickers."}
    assert lines[1] == {"speaker": "mara", "text": "You left the door open.", "emotion": "worried"}
    assert lines[2] == {"speaker": "jonas", "text": "I left it open for you."}

    _, err = parse_screenplay("GHOST: boo", chars)
    assert "unknown speaker" in err and "mara" in err
    _, err = parse_screenplay("MARA [weary]: hm", chars)
    assert "weary" in err
    _, err = parse_screenplay("just prose with no speaker", chars)
    assert "NAME:" in err


def test_write_scene_stores_parsed_node(tmp_path):
    state = RunState(tmp_path)
    state.write_component("characters", {"characters": [{"id": "mara", "name": "Mara"},
                                                        {"id": "jonas", "name": "Jonas"}]})
    tools = build_tools(_spec(), state)
    res = tools["write_scene"](
        "scene_01",
        "MARA: Hand me the crate.\nJONAS [angry]: Get your own.\nNARR: He turns away.",
        {"type": "jump", "target": "scene_02"},
        location="bg_hall", beat="beat_01", event_summary="crate standoff")
    assert res["ok"] is True, res
    node = state.read_component("nodes")["nodes"]["scene_01"]
    assert node["beat"] == "beat_01" and node["location"] == "bg_hall"
    assert node["lines"][1] == {"speaker": "jonas", "text": "Get your own.", "emotion": "angry"}
    assert state.read_component("nodes")["synopses"]["scene_01"] == "crate standoff"

    bad = tools["write_scene"]("s2", "MARA: hi", {"type": "menu", "choices": [
        {"text": "a", "target": "x"}, {"text": "b", "target": "x"}]})
    assert bad["ok"] is False and "fake choice" in bad["error"]


def test_missing_location_is_patched_not_rejected(tmp_path):
    # A reject forces a full-scene regen and retries degrade; a missing/wrong location is a
    # one-field repair, so the write is ACCEPTED and the location check flags it for a patch.
    from maestro.modules.scenes import each_node_has_location
    state = RunState(tmp_path)
    state.write_component("asset_manifest", {"backgrounds": [{"id": "bg_apartment"}],
                                             "characters": []})
    tools = build_tools(_spec(), state)
    node = {"lines": [{"speaker": "a", "text": "hi"}], "end": {"type": "return"}}
    assert tools["write_node"]("s1", node)["ok"] is True

    def artifact():
        return {"nodes": state.read_component("nodes"),
                "asset_manifest": state.read_component("asset_manifest")}

    ok, msg = each_node_has_location(artifact())
    assert ok is False and "s1" in msg and "bg_apartment" in msg
    res = tools["edit_node"]("s1", location="bg_nowhere")
    assert res["ok"] is False and "bg_apartment" in res["error"]
    assert tools["edit_node"]("s1", location="bg_apartment")["ok"] is True
    assert each_node_has_location(artifact())[0] is True


def test_location_check_flags_unknown_background(tmp_path):
    from maestro.modules.scenes import each_node_has_location
    art = {"nodes": {"node_ids": ["s1"],
                     "nodes": {"s1": {"location": "bg_ghost", "lines": [{"text": "x"}],
                                      "end": {"type": "return"}}}},
           "asset_manifest": {"backgrounds": [{"id": "bg_real"}]}}
    ok, msg = each_node_has_location(art)
    assert ok is False and "s1" in msg and "bg_real" in msg


def test_write_node_rejects_bad_emotion(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    bad = {"lines": [{"speaker": "a", "text": "hi", "emotion": "weary"}],
           "end": {"type": "return"}}
    res = tools["write_node"]("s1", bad)
    assert res["ok"] is False and "weary" in res["error"]
    good = {"lines": [{"speaker": "a", "text": "hi", "emotion": "worried"}],
            "end": {"type": "return"}}
    assert tools["write_node"]("s1", good)["ok"] is True


def test_write_node_enforces_min_lines_floor(tmp_path):
    # When the spec demands each_node_min_lines, a thin node is rejected at write time
    # (born-compliant) so the loop never enters a separate repair phase for it.
    spec = Spec({"title": "T", "frozen": True, "modules": [],
                 "params": {"each_node_min_lines": 3}})
    tools = build_tools(spec, RunState(tmp_path))
    thin = {"lines": [{"speaker": "a", "text": "hi"}], "end": {"type": "return"}}
    res = tools["write_node"]("s1", thin)
    assert res["ok"] is False and "at least 3" in res["error"]
    fat = {"lines": [{"text": "a"}, {"text": "b"}, {"text": "c"}], "end": {"type": "return"}}
    assert tools["write_node"]("s1", fat)["ok"] is True


def test_write_node_caps_menu_width(tmp_path):
    # A wide menu is the hub-and-spoke star that guts the arc: rejected at write time so the
    # slot-driven loop can't hit its node quota by fanning one node into many stub branches.
    tools = build_tools(_spec(), RunState(tmp_path))
    wide = {"lines": [{"speaker": "a", "text": "go where?"}],
            "end": {"type": "menu", "choices": [
                {"text": f"room {i}", "target": f"scene_{i}"} for i in range(5)]}}
    res = tools["write_node"]("hub", wide)
    assert res["ok"] is False and "dramatic fork" in res["error"].lower()

    ok = {"lines": [{"speaker": "a", "text": "go where?"}],
          "end": {"type": "menu", "choices": [
              {"text": "open it", "target": "ending_truth"},
              {"text": "leave it", "target": "ending_silence"}]}}
    assert tools["write_node"]("fork", ok)["ok"] is True
    # linear flow is unaffected — a jump can lead anywhere, no width limit applies.
    assert tools["write_node"]("s1", _node(end={"type": "jump", "target": "s2"}))["ok"] is True


def test_write_node_rejects_all_gated_menu(tmp_path):
    # A menu where every choice is gated by `requires` can come up empty at runtime → dead-end.
    # Require at least one unconditional fallback.
    tools = build_tools(_spec(), RunState(tmp_path))
    all_gated = {"lines": [{"speaker": "a", "text": "the crisis"}],
                 "end": {"type": "menu", "choices": [
                     {"text": "earned", "target": "ending_truth",
                      "requires": {"var": "trust", "op": ">=", "value": 2}},
                     {"text": "also gated", "target": "ending_silence",
                      "requires": {"var": "trust", "op": "<", "value": 0}}]}}
    res = tools["write_node"]("crisis", all_gated)
    assert res["ok"] is False and "fallback" in res["error"].lower()

    with_fallback = {"lines": [{"speaker": "a", "text": "the crisis"}],
                     "end": {"type": "menu", "choices": [
                         {"text": "earned", "target": "ending_truth",
                          "requires": {"var": "trust", "op": ">=", "value": 2}},
                         {"text": "fallback", "target": "ending_silence"}]}}
    assert tools["write_node"]("crisis", with_fallback)["ok"] is True


def test_write_node_rejects_fake_fork(tmp_path):
    # A menu whose choices all lead to the same scene is illusory agency — rejected.
    tools = build_tools(_spec(), RunState(tmp_path))
    fake = {"lines": [{"speaker": "a", "text": "pick one"}],
            "end": {"type": "menu", "choices": [
                {"text": "left", "target": "scene_02"},
                {"text": "right", "target": "scene_02"}]}}
    res = tools["write_node"]("fork", fake)
    assert res["ok"] is False and "same scene" in res["error"].lower()

    real = {"lines": [{"speaker": "a", "text": "pick one"}],
            "end": {"type": "menu", "choices": [
                {"text": "left", "target": "scene_02"},
                {"text": "right", "target": "scene_03"}]}}
    assert tools["write_node"]("fork", real)["ok"] is True


def test_read_node_returns_object(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node("yo"))
    got = tools["read_node"]("s1")
    assert got["ok"] is True and got["content"]["lines"][0]["text"] == "yo"
    assert tools["read_node"]("missing")["ok"] is False


# ── structured place tools ───────────────────────────────────────────────────

def _place():
    return {"kind": "room", "background": "bg_x", "interactables": [
        {"id": "hs_door", "label": "Door", "position": {"rect": {"x": 0, "y": 0, "w": 10, "h": 10}},
         "action": {"type": "move", "target": "room_b"}}]}


def test_write_and_read_place(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    assert tools["write_place"]("room_a", _place())["ok"] is True
    pl = state.read_component("places")
    assert pl["place_ids"] == ["room_a"] and pl["start_place"] == "room_a"
    assert tools["read_place"]("room_a")["content"]["background"] == "bg_x"
    assert tools["write_place"]("room_b", {"interactables": []})["ok"] is False  # empty


def test_write_component_rejects_place_ids_without_entries(tmp_path):
    # A places component listing place_ids with no matching `places` entry is internally
    # inconsistent: the create-guard sees the id (already-exists) while add_interactable/
    # edit_place can't find it (no place) — an unfixable stall. Reject it at write time.
    from maestro.modules import compose
    spec = {"frozen": True, "modules": ["world"], "params": {}}
    tools = build_tools(spec, RunState(tmp_path), compose(("world",)))
    res = tools["write_component"]("places", {"place_ids": ["room_a", "room_b"], "places": {}})
    assert res["ok"] is False and "no entry in places.places" in res["error"]
    # The reverse mismatch (entry not listed in place_ids) is rejected too.
    res = tools["write_component"]("places",
                                   {"place_ids": ["room_a"], "places": {"room_a": _place(),
                                                                        "room_b": _place()}})
    assert res["ok"] is False and "not listed in place_ids" in res["error"]
    # A consistent component passes.
    assert tools["write_component"]("places",
                                    {"place_ids": ["room_a"], "places": {"room_a": _place()}})["ok"]


def test_set_places_meta_and_edit_place(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_place"]("room_a", _place())
    tools["set_places_meta"](goal={"type": "flag", "id": "escaped"}, flags=["escaped"])
    pl = state.read_component("places")
    assert pl["goal"] == {"type": "flag", "id": "escaped"} and pl["flags"] == ["escaped"]
    assert tools["set_places_meta"](items=[{"id": "key"}])["ok"] is False  # items live in `items` now
    # repoint the move target
    assert tools["edit_place"]("room_a", "hs_door",
                               action={"type": "move", "target": "room_c"})["ok"] is True
    h = state.read_component("places")["places"]["room_a"]["interactables"][0]
    assert h["action"]["target"] == "room_c"
    assert tools["set_places_meta"](goal={"type": "x"})["ok"] is False  # bad goal shape


# ── locking ──────────────────────────────────────────────────────────────────

def _dep_spec():
    # cast's `characters` locks once complete; the scenes terminal owns `nodes`
    # (emits_compile → never locks, stays writable to the end).
    return {"title": "T", "frozen": True, "modules": ["cast", "scenes"], "params": {}}


def _dep_modules():
    from maestro.modules import compose
    return compose(("cast", "scenes"))


_FULL_CAST = {"characters": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}


def test_passing_component_locks_against_rewrite(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_dep_spec(), state, _dep_modules())
    assert tools["write_component"]("characters", _FULL_CAST)["ok"]   # now satisfies cast
    res = tools["write_component"]("characters", {"characters": [{"id": "a", "name": "A"}]})
    assert res["ok"] is False and "locked" in res["error"]
    assert len(state.read_component("characters")["characters"]) == 2


def test_state_wiring_error_on_component_keeps_it_writable(tmp_path):
    # items passes its OWN checks but a state_wiring error lands on it ("cut the declaration") —
    # the lock must not refuse the very fix the error asks for.
    from maestro.modules import compose
    spec = {"title": "T", "frozen": True, "modules": ["cast", "world", "inventory", "state"],
            "params": {}}
    modules = compose(("cast", "world", "inventory", "state"))
    state = RunState(tmp_path)
    tools = build_tools(spec, state, modules)
    assert tools["write_component"]("items", {"items": [
        {"id": "item_orphan", "name": "Orphan"}]})["ok"]   # satisfies inventory's checks
    # state_wiring: item_orphan declared, never produced/consumed → error hosted on items
    res = tools["write_component"]("items", {"items": [
        {"id": "item_orphan2", "name": "Other"}]})
    assert res["ok"] is True   # cut/rewrite allowed while the wiring error is open


def test_human_todo_on_component_keeps_it_writable(tmp_path):
    from maestro.modules import human
    state = RunState(tmp_path)
    tools = build_tools(_dep_spec(), state, _dep_modules())
    assert tools["write_component"]("characters", _FULL_CAST)["ok"]   # locks
    human.add_todo(state, "characters", "make the villain meaner")
    res = tools["write_component"]("characters", _FULL_CAST)
    assert res["ok"] is True   # open human note targets it → writable


def test_leaf_component_never_locks(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_dep_spec(), state, _dep_modules())
    # nodes is the compile terminal (emits_compile) → never locks, stays writable.
    n = {"location": "bg", "lines": [{"speaker": "a", "text": "x"}], "end": {"type": "return"}}
    assert tools["write_node"]("s1", n)["ok"]
    assert tools["write_node"]("s2", n)["ok"]
