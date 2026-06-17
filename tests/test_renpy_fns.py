"""Tests for renpy dialogue parsing helpers (no LLM calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from renpy.fns import (
    _merge_cast_into_manifest,
    _parse_char_output,
    _split_dialogue,
    _guaranteed_ancestors,
    _validate_sketch,
    _exit_note,
    _history_str,
    _generate_voice_sheets,
    _ending_slots,
    _repair_spine,
    _find_protagonist_id,
    _MAX_LINE_CHARS,
)
from renpy.graph import assemble_story


# ---------------------------------------------------------------------------
# _split_dialogue
# ---------------------------------------------------------------------------

def test_split_short_line_unchanged():
    assert _split_dialogue("Hello there.") == ["Hello there."]


def test_split_long_line_at_sentence():
    long = ("She turned to face him. " * 15).strip()
    boxes = _split_dialogue(long)
    assert len(boxes) > 1
    for box in boxes:
        assert len(box) <= _MAX_LINE_CHARS or " " not in box


def test_split_preserves_content():
    text = "First sentence. Second sentence. Third sentence."
    joined = " ".join(_split_dialogue(text))
    assert "First sentence" in joined
    assert "Third sentence" in joined


# ---------------------------------------------------------------------------
# _parse_char_output — plain speech
# ---------------------------------------------------------------------------

def test_plain_speech():
    lines, hist = _parse_char_output("Hello there, nice to meet you.", "elena")
    assert lines == ['    elena "Hello there, nice to meet you."']
    assert "Hello there" in hist


def test_strips_speaker_prefix():
    lines, hist = _parse_char_output("Elena: Hello there.", "elena")
    assert lines == ['    elena "Hello there."']


def test_strips_outer_quotes():
    # Outer quotes on input should not produce doubled quotes in output
    lines, hist = _parse_char_output('"Hello there."', "elena")
    assert lines == ['    elena "Hello there."']


def test_inner_quotes_become_single():
    lines, _ = _parse_char_output('He said "goodbye" to her.', "elena")
    assert '"goodbye"' not in lines[0]
    assert "'goodbye'" in lines[0]


# ---------------------------------------------------------------------------
# _parse_char_output — action segments
# ---------------------------------------------------------------------------

def test_action_becomes_narrator_line():
    lines, hist = _parse_char_output("*She smiles warmly.* I'm glad you came.", "elena")
    assert '    act "She smiles warmly."' in lines
    assert any("elena" in ln for ln in lines)


def test_action_at_end():
    lines, _ = _parse_char_output("I knew you'd come. *She turns away.*", "elena")
    assert any('act "She turns away."' in ln for ln in lines)
    assert any("elena" in ln for ln in lines)


def test_action_only():
    lines, hist = _parse_char_output("*She stands there in silence.*", "elena")
    assert lines == ['    act "She stands there in silence."']


def test_action_between_speech():
    lines, _ = _parse_char_output(
        "I've been waiting. *She looks away.* But not anymore.", "elena"
    )
    assert len(lines) == 3
    assert '    act "She looks away."' in lines
    assert lines[0].startswith("    elena")
    assert lines[2].startswith("    elena")


# ---------------------------------------------------------------------------
# _parse_char_output — curly quotes + screenplay-style stage directions
# ---------------------------------------------------------------------------

def test_curly_quotes_normalized():
    lines, _ = _parse_char_output("“Hello there.”", "elena")
    assert lines == ['    elena "Hello there."']


def test_curly_inner_quotes_become_single():
    lines, _ = _parse_char_output("He said “goodbye” to her.", "elena")
    assert len(lines) == 1
    assert "'goodbye'" in lines[0]


def test_screenplay_stage_direction_becomes_action():
    raw = '“Give me the waiver now.” She leans forward, eyes locked.'
    lines, _ = _parse_char_output(raw, "elena")
    assert '    elena "Give me the waiver now."' in lines
    assert '    act "She leans forward, eyes locked."' in lines


def test_screenplay_two_quoted_spans():
    raw = '“Delay the waiver and our lights stay mute.” “My family’s legacy will vanish.”'
    lines, _ = _parse_char_output(raw, "elena")
    assert all(ln.startswith("    elena") for ln in lines)
    assert "lights stay mute" in lines[0]


def test_screenplay_attribution_dropped():
    raw = '"We leave at dawn," she whispers.'
    lines, _ = _parse_char_output(raw, "elena")
    assert lines == ['    elena "We leave at dawn,"']


def test_mid_sentence_quote_left_alone():
    # quote not at line start = dialogue content, not screenplay formatting
    lines, _ = _parse_char_output('You called it "temporary" for six years.', "elena")
    assert len(lines) == 1
    assert "'temporary'" in lines[0]


# ---------------------------------------------------------------------------
# _parse_char_output — tilde emphasis stripped to plain text
# ---------------------------------------------------------------------------

def test_tilde_stripped_to_plain():
    lines, _ = _parse_char_output("That was ~the last time~.", "elena")
    assert "{b}" not in lines[0]
    assert "the last time" in lines[0]
    assert lines[0].startswith("    elena")


def test_tilde_inline_plain():
    lines, _ = _parse_char_output("I loved him. That was ~everything~.", "elena")
    assert len(lines) == 1
    assert "everything" in lines[0]
    assert "{b}" not in lines[0]


# ---------------------------------------------------------------------------
# _parse_char_output — mixed: action kept, tilde stripped
# ---------------------------------------------------------------------------

def test_mixed_action_and_tilde():
    raw = "*She grabs his arm.* Don't go. ~Please.~"
    lines, hist = _parse_char_output(raw, "elena")
    assert any('act "She grabs his arm."' in ln for ln in lines)
    assert any("elena" in ln for ln in lines)
    assert not any("{b}" in ln for ln in lines)


def test_empty_input():
    lines, hist = _parse_char_output("", "elena")
    assert lines == []
    assert hist.strip() == ""


# ---------------------------------------------------------------------------
# _parse_char_output — emoji, raw newlines, unbalanced quotes
# ---------------------------------------------------------------------------

def test_emoji_stripped():
    lines, _ = _parse_char_output("Best day ever! 🎢😄", "elena")
    assert lines == ['    elena "Best day ever!"']


def test_newlines_collapsed_into_one_say_string():
    lines, _ = _parse_char_output("Stay close.\nDon't wander off.", "elena")
    assert lines == ['    elena "Stay close. Don\'t wander off."']
    assert not any("\n" in ln for ln in lines)


def test_unbalanced_quote_dropped_not_converted():
    lines, _ = _parse_char_output("I won't let you drift.\" She nods.", "elena")
    assert len(lines) == 1
    assert "drift. She nods." in lines[0]
    assert "drift.'" not in lines[0]


# ---------------------------------------------------------------------------
# assemble_story — outline → graph + beat_map
# ---------------------------------------------------------------------------

def _scene(summary, stype="confrontation"):
    return {"summary": summary, "scene_type": stype, "whats_new": "something changed",
            "location_id": "park", "characters_present": ["mara"], "emotional_tone": "tense"}


def _story():
    return {
        "central_question": "hold on or let go?",
        "endings": [
            {"id": "ending_001", "end_type": "good", "title": "Let Go", "summary": "a",
             "tone": "lighter", "location_id": "gate", "characters_present": ["mara"]},
            {"id": "ending_002", "end_type": "bad", "title": "Grip", "summary": "b",
             "tone": "hollow", "location_id": "gate", "characters_present": ["mara"]},
            {"id": "ending_003", "end_type": "neutral", "title": "Photo", "summary": "c",
             "tone": "rueful", "location_id": "exit", "characters_present": ["mara"]},
        ],
        "commitment_choice": {"situation": "kid bolts", "options": [
            {"label": "Chase him", "strategy": "control", "ending_ids": ["ending_001", "ending_002"]},
            {"label": "Cover for him", "strategy": "trust", "ending_ids": ["ending_003"]},
        ]},
        "trunk": [_scene("arrive", "respite"), _scene("lunch tension", "complication"), _scene("kid bolts", "decision")],
        "arms": [
            {"label": "Chase him", "strategy": "control", "ending_ids": ["ending_001", "ending_002"],
             "crisis_labels": ["Apologize", "Double down"],
             "scenes": [_scene("finds him"), _scene("exit fight")]},
            {"label": "Cover for him", "strategy": "trust", "ending_ids": ["ending_003"],
             "scenes": [_scene("lies to dad", "complication"), _scene("kid returns", "revelation")]},
        ],
    }


def test_assemble_topology():
    g = assemble_story(_story())["graph"]
    nodes = g["nodes"]
    assert g["root_id"] == "root"
    assert nodes["root"]["type"] == "root"
    # trunk: root → beat → commitment branch with one child per arm
    assert nodes["branch_001"]["type"] == "branch"
    assert len(nodes["branch_001"]["child_ids"]) == 2
    # two-ending arm closes with a crisis branch into its endings
    assert nodes["branch_002"]["child_ids"] == ["ending_001", "ending_002"]
    # single-ending arm flows straight into its ending
    ending3_parents = nodes["ending_003"]["parent_ids"]
    assert len(ending3_parents) == 1 and nodes[ending3_parents[0]]["type"] == "beat"
    # everything reachable from root
    assert set(nodes["root"]["reachable_endings"]) == {"ending_001", "ending_002", "ending_003"}


def test_assemble_beat_map_choice_labels():
    out = assemble_story(_story())
    bm = out["beat_map"]
    assert bm["branch_001"]["choice_labels"] == ["Chase him", "Cover for him"]
    assert bm["branch_002"]["choice_labels"] == ["Apologize", "Double down"]
    assert bm["beat_001"]["choice_labels"] == []
    # whats_new becomes dramatic_purpose; endings pass through verbatim
    assert bm["root"]["dramatic_purpose"] == "something changed"
    assert bm["ending_001"]["title"] == "Let Go"


def test_assemble_when_passthrough():
    story = _story()
    story["trunk"][0]["when"] = "morning, just inside the gates"
    bm = assemble_story(story)["beat_map"]
    assert bm["root"]["when"] == "morning, just inside the gates"
    assert bm["beat_001"]["when"] == ""


def test_assemble_crisis_labels_fallback_to_titles():
    story = _story()
    del story["arms"][0]["crisis_labels"]
    bm = assemble_story(story)["beat_map"]
    assert bm["branch_002"]["choice_labels"] == ["Let Go", "Grip"]


def test_assemble_rejects_broken_outlines():
    story = _story()
    story["trunk"] = [_scene("only one")]
    with pytest.raises(ValueError):
        assemble_story(story)

    story = _story()
    story["arms"][0]["scenes"] = []
    with pytest.raises(ValueError):
        assemble_story(story)

    story = _story()
    story["arms"][0]["ending_ids"] = ["ending_nope"]
    with pytest.raises(ValueError):
        assemble_story(story)


# ---------------------------------------------------------------------------
# story stage helpers
# ---------------------------------------------------------------------------

def test_ending_slots_types_and_ids():
    slots = _ending_slots(4, 2)
    assert [s["id"] for s in slots] == ["ending_001", "ending_002", "ending_003", "ending_004"]
    types = [s["end_type"] for s in slots]
    assert types[:2] == ["good", "good"]
    assert types[2] == "bad"


def test_repair_spine_partitions_all_endings():
    eids = ["ending_001", "ending_002", "ending_003"]
    # duplicate assignment + missing ending + unknown id
    spine = {"commitment_choice": {"options": [
        {"label": "A", "ending_ids": ["ending_001", "ending_001", "ending_ghost"]},
        {"label": "B", "ending_ids": ["ending_001"]},
    ]}}
    fixed = _repair_spine(spine, eids)
    opts = fixed["commitment_choice"]["options"]
    assigned = [e for o in opts for e in o["ending_ids"]]
    assert sorted(assigned) == sorted(eids)
    assert all(o["ending_ids"] for o in opts)


def test_repair_spine_handles_empty():
    fixed = _repair_spine({}, ["ending_001", "ending_002"])
    opts = fixed["commitment_choice"]["options"]
    assert len(opts) == 2
    assert all(len(o["ending_ids"]) == 1 for o in opts)


def test_find_protagonist_uses_premise_field():
    premise = {"protagonist_id": "beta",
               "characters": [{"id": "alpha"}, {"id": "beta"}]}
    assert _find_protagonist_id(premise) == "beta"
    assert _find_protagonist_id({"characters": [{"id": "alpha"}]}) == "alpha"
    assert _find_protagonist_id({"protagonist_id": "ghost", "characters": [{"id": "alpha"}]}) == "alpha"


def test_generate_premise_backfills_missing_protagonist_id(monkeypatch):
    from renpy import fns as F

    # Model omits protagonist_id entirely
    monkeypatch.setattr(F, "_call_json", lambda *a, **k: {
        "premise": "p", "central_question": "q",
        "characters": [{"id": "mara", "name": "Mara"}, {"id": "kai", "name": "Kai"}],
    })
    monkeypatch.setattr(F, "_generate_voice_sheets", lambda *a, **k: None)

    result = F.generate_premise({"brief": {"genre": "drama"}}, Path("/tmp"))
    assert result["protagonist_id"] == "mara"


def test_generate_premise_repairs_unresolved_protagonist_id(monkeypatch):
    from renpy import fns as F

    # Model emits an id that matches no character
    monkeypatch.setattr(F, "_call_json", lambda *a, **k: {
        "premise": "p", "protagonist_id": "ghost",
        "characters": [{"id": "mara", "name": "Mara"}],
    })
    monkeypatch.setattr(F, "_generate_voice_sheets", lambda *a, **k: None)

    result = F.generate_premise({"brief": {}}, Path("/tmp"))
    assert result["protagonist_id"] == "mara"


# ---------------------------------------------------------------------------
# _guaranteed_ancestors — story-so-far must exclude sibling branches
# ---------------------------------------------------------------------------

def _diamond_dag():
    # root → branch → (beat_a | beat_b) → merge → ending
    nodes = {
        "root":    {"parent_ids": [],                   "child_ids": ["branch"]},
        "branch":  {"parent_ids": ["root"],             "child_ids": ["beat_a", "beat_b"]},
        "beat_a":  {"parent_ids": ["branch"],           "child_ids": ["merge"]},
        "beat_b":  {"parent_ids": ["branch"],           "child_ids": ["merge"]},
        "merge":   {"parent_ids": ["beat_a", "beat_b"], "child_ids": ["ending"]},
        "ending":  {"parent_ids": ["merge"],            "child_ids": []},
    }
    topo = ["root", "branch", "beat_a", "beat_b", "merge", "ending"]
    return nodes, topo


def test_ancestors_linear_chain():
    nodes, topo = _diamond_dag()
    anc = _guaranteed_ancestors(nodes, topo)
    assert anc["root"] == []
    assert anc["branch"] == ["root"]
    assert anc["beat_a"] == ["root", "branch"]


def test_ancestors_merge_excludes_sibling_branches():
    nodes, topo = _diamond_dag()
    anc = _guaranteed_ancestors(nodes, topo)
    # Player reached merge via beat_a OR beat_b — neither is guaranteed
    assert anc["merge"] == ["root", "branch"]
    assert anc["ending"] == ["root", "branch", "merge"]


def test_ancestors_sibling_not_in_other_path():
    nodes, topo = _diamond_dag()
    anc = _guaranteed_ancestors(nodes, topo)
    assert "beat_b" not in anc["beat_a"]
    assert "beat_a" not in anc["beat_b"]


# ---------------------------------------------------------------------------
# _validate_sketch
# ---------------------------------------------------------------------------

def test_sketch_maps_narrator_and_filters_unknown_speakers():
    sketch = {
        "lines": [
            {"speaker": "narrator", "intent": "set the scene", "emotion": "ominous"},
            {"speaker": "elena", "intent": "demands answers", "emotion": "angry"},
            {"speaker": "ghost_of_elvis", "intent": "haunts", "emotion": "spooky"},
            "not a dict",
            {"speaker": "marcus", "intent": "deflects", "emotion": "nervous"},
        ]
    }
    slots = _validate_sketch(sketch, ["elena", "marcus"])
    assert [s["type"] for s in slots] == ["narration", "line", "line"]
    assert slots[1]["speaker"] == "elena"
    assert slots[1]["intent"] == "demands answers"
    assert slots[2]["speaker"] == "marcus"


def test_sketch_empty_or_malformed():
    assert _validate_sketch({}, ["elena"]) == []
    assert _validate_sketch({"lines": "nope"}, ["elena"]) == []


def test_sketch_register_passthrough_defaults_plain():
    sketch = {"lines": [
        {"speaker": "elena", "intent": "x", "emotion": "y", "register": "charged"},
        {"speaker": "elena", "intent": "x", "emotion": "y"},
    ]}
    slots = _validate_sketch(sketch, ["elena"])
    assert slots[0]["register"] == "charged"
    assert slots[1]["register"] == "plain"


# ---------------------------------------------------------------------------
# _exit_note
# ---------------------------------------------------------------------------

def test_exit_note_branch_names_choices():
    note = _exit_note("branch", {}, ["a", "b"], ["Trust her", "Run"], {})
    assert "Trust her" in note and "Run" in note
    assert "choosing" in note


def test_exit_note_beat_flows_into_next():
    beat_map = {"next_node": {"summary": "Elena confronts Marcus on the roof."}}
    note = _exit_note("beat", {}, ["next_node"], [], beat_map)
    assert "Elena confronts Marcus" in note


def test_exit_note_ending_demands_finality():
    note = _exit_note("ending", {"summary": "She walks away forever."}, [], [], {})
    assert "final" in note.lower()
    assert "She walks away forever." in note


def test_exit_note_empty_when_nothing_known():
    assert _exit_note("beat", {}, [], [], {}) == ""


# ---------------------------------------------------------------------------
# _history_str — display names, not snake_case ids
# ---------------------------------------------------------------------------

def test_history_uses_display_names():
    history = [
        {"type": "line", "speaker": "elena_voss", "speaker_name": "Elena", "text": "Hello."},
        {"type": "narration", "text": "A pause."},
    ]
    out = _history_str(history)
    assert "Elena: Hello." in out
    assert "elena_voss" not in out
    assert "[narration]: A pause." in out


# ---------------------------------------------------------------------------
# _generate_voice_sheets — one call per character, sheet stored on character
# ---------------------------------------------------------------------------

def test_voice_sheets_per_character(monkeypatch):
    import renpy.fns as fns

    sent_prompts = []

    class FakeAgent:
        def __init__(self, system, max_tokens=None):
            pass
        def send(self, prompt):
            sent_prompts.append(prompt)
            return f"VOICE SHEET {len(sent_prompts)}"

    monkeypatch.setattr(fns, "PipelineAgent", FakeAgent)

    premise = {
        "premise": "Two rivals share a lighthouse.",
        "tone_directives": [{"adjective": "tense", "explanation": "clipped speech"}],
        "characters": [
            {"id": "elena", "name": "Elena", "role": "protagonist", "voice": "clipped, formal"},
            {"id": "marcus", "name": "Marcus", "role": "rival", "voice": "rambling, warm"},
        ],
    }
    _generate_voice_sheets(premise)

    assert len(sent_prompts) == 2
    assert premise["characters"][0]["voice_mechanics"] == "VOICE SHEET 1"
    assert premise["characters"][1]["voice_mechanics"] == "VOICE SHEET 2"
    # Each sheet prompt sees the other characters' voices for contrast
    assert "rambling, warm" in sent_prompts[0]
    assert "clipped, formal" in sent_prompts[1]


# ---------------------------------------------------------------------------
# _copy_templates — seeds missing gitignored assets from the SDK
# ---------------------------------------------------------------------------

def test_copy_templates_seeds_gui_from_sdk(tmp_path, monkeypatch):
    import renpy.renpy_builder as rb

    sdk_game = tmp_path / "sdk" / "the_question" / "game"
    (sdk_game / "gui").mkdir(parents=True)
    (sdk_game / "gui" / "frame.png").write_bytes(b"\x89PNG fake")
    (sdk_game / "screens.rpy").write_text("# screens")
    (sdk_game / "gui.rpy").write_text("# gui")

    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "gui.rpy").write_text("# customized gui")
    monkeypatch.setattr(rb, "TEMPLATES_DIR", str(templates))

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    rb._copy_templates(str(game_dir), str(tmp_path / "sdk"))

    # missing pieces seeded from SDK, existing customization untouched
    assert (templates / "gui" / "frame.png").exists()
    assert (templates / "screens.rpy").read_text() == "# screens"
    assert (templates / "gui.rpy").read_text() == "# customized gui"
    # and copied into the game
    assert (game_dir / "gui" / "frame.png").exists()
    assert (game_dir / "screens.rpy").exists()


def test_copy_templates_no_sdk_still_warns_not_crashes(tmp_path, monkeypatch):
    import renpy.renpy_builder as rb

    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(rb, "TEMPLATES_DIR", str(templates))

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    rb._copy_templates(str(game_dir), "")
    assert not (game_dir / "gui").exists()


# ---------------------------------------------------------------------------
# generate_images — placeholder fallback when ComfyUI fails
# ---------------------------------------------------------------------------

def test_generate_images_writes_placeholders_on_failure(tmp_path, monkeypatch):
    import tools.comfyui_tools as comfyui_tools
    from renpy.fns import generate_images

    monkeypatch.setattr(
        comfyui_tools, "generate_images_batch",
        lambda jobs: [{"success": False, "error": "no comfyui"} for _ in jobs],
    )

    inputs = {
        "premise": {"characters": [{"id": "alex", "name": "Alex", "appearance": "tall"}]},
        "asset_manifest": {
            "backgrounds": [{"id": "bg_dock", "image_file": "dock.png", "description": "a dock"}],
            "characters":  [{"id": "alex", "name": "Alex", "image_file": "alex.png"}],
            "cgs":         [{"id": "cg_finale", "image_file": "cg_finale.png", "description": "finale"}],
            "title_card":  {"image_file": "title_card.png", "description": "title"},
        },
    }

    result = generate_images(inputs, tmp_path)

    assert result["status"] == "ok"
    assert result["generated"] == []
    assert {f["file"] for f in result["failed"]} == {"dock.png", "alex.png", "cg_finale.png", "title_card.png"}
    images_dir = tmp_path / "game_output" / "game" / "images"
    for name in ("dock.png", "alex.png", "cg_finale.png", "title_card.png"):
        png = (images_dir / name).read_bytes()
        assert png.startswith(b"\x89PNG")


# ---------------------------------------------------------------------------
# generate_single_node — scene_position plumbing (LLM calls stubbed)
# ---------------------------------------------------------------------------

def test_generate_single_node_scene_position(monkeypatch):
    from renpy import fns as F

    premise = {
        "premise": "p", "tone_directives": [], "setting": {},
        "characters": [{"id": "mara", "name": "Mara", "voice": "v"}],
        "protagonist_id": "mara",
    }
    out = assemble_story(_story())
    dag, bm = out["graph"], out["beat_map"]
    bm["beat_001"]["when"] = "midday, after lunch"

    captured = {}
    real_render = F.render_template

    def spy(path, ctx):
        if Path(path).name == "character_line.txt":
            captured["pos"] = ctx.get("scene_position")
        return real_render(path, ctx)

    monkeypatch.setattr(F, "render_template", spy)
    monkeypatch.setattr(F, "_generate_scene_sketch", lambda *a, **k: {
        "lines": [{"speaker": "mara", "intent": "x", "emotion": "y"}] * 4})
    monkeypatch.setattr(F, "_call_char_line", lambda s, u: "Hello.")
    monkeypatch.setattr(F, "_call_line", lambda s, u: "Quiet.")

    script = F.generate_single_node(premise, dag, bm, "beat_001")
    assert captured["pos"] == "scene 2 of this playthrough — midday, after lunch"
    assert script.startswith("label beat_001:")

    F.generate_single_node(premise, dag, bm, "root")
    assert captured["pos"] == "the story's opening scene"


# ---------------------------------------------------------------------------
# scene CLI — run dir loading and node listing (no LLM)
# ---------------------------------------------------------------------------

def test_scene_cli_load_and_list(tmp_path, capsys):
    import json
    from renpy import scene

    out = assemble_story(_story())
    (tmp_path / "premise.json").write_text(json.dumps({"characters": []}))
    (tmp_path / "graph.json").write_text(json.dumps(out["graph"]))
    (tmp_path / "beat_map.json").write_text(json.dumps({"beat_map": out["beat_map"]}))

    assert scene.main([str(tmp_path)]) == 0
    listing = capsys.readouterr().out
    assert "root" in listing and "ending_003" in listing

    assert scene.main([str(tmp_path), "not_a_node"]) == 2

    with pytest.raises(FileNotFoundError):
        scene.main([str(tmp_path / "nope")])


# ---------------------------------------------------------------------------
# _merge_cast_into_manifest — premise is the source of truth for the cast
# ---------------------------------------------------------------------------

def test_merge_backfills_empty_manifest_from_premise():
    premise = {"characters": [{"id": "jack", "name": "Jack", "voice": "gruff"},
                              {"id": "mara", "name": "Mara", "description": "informant"}]}
    merged = _merge_cast_into_manifest(premise, {"characters": []})
    ids = {c["id"] for c in merged["characters"]}
    assert ids == {"jack", "mara"}
    by_id = {c["id"]: c for c in merged["characters"]}
    assert by_id["jack"]["image_file"] == "jack.png"
    assert by_id["mara"]["description"] == "informant"


def test_merge_preserves_existing_manifest_entry_as_override():
    premise = {"characters": [{"id": "jack", "name": "Jack"}]}
    manifest = {"characters": [{"id": "jack", "image_file": "custom_jack.png", "description": "art note"}]}
    merged = _merge_cast_into_manifest(premise, manifest)
    assert len(merged["characters"]) == 1
    assert merged["characters"][0]["image_file"] == "custom_jack.png"


def test_merge_keeps_other_manifest_keys():
    premise = {"characters": [{"id": "jack", "name": "Jack"}]}
    manifest = {"backgrounds": [{"id": "bg_x"}], "characters": []}
    merged = _merge_cast_into_manifest(premise, manifest)
    assert merged["backgrounds"] == [{"id": "bg_x"}]
    assert {c["id"] for c in merged["characters"]} == {"jack"}
