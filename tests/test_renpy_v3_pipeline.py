"""Tests for renpy pipeline structure (no LLM calls required)."""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipelines.renpy.fns import (
    _bible_summary,
    _character_vars_block,
    _validate_brief,
    _validate_scene_script,
    _find_script_issues,
    _stitch_script,
    _scenes_for_character,
)
from pipelines.registry import get_registry


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_renpy_registered():
    registry = get_registry()
    assert "renpy" in registry
    defn = registry["renpy"]
    assert defn.pipeline is not None
    assert defn.enrich_brief is not None


def test_renpy_pipeline_nodes():
    registry   = get_registry()
    pipeline   = registry["renpy"].pipeline
    node_ids   = [n.id for n in pipeline.nodes]
    assert node_ids == [
        "bible",
        "scene_plan",
        "asset_manifest",
        "scene_scripts",
        "continuity",
        "images",
        "build",
    ]


def test_prompt_files_exist():
    prompts_dir = Path(__file__).parent.parent / "src" / "pipelines" / "renpy" / "prompts"
    expected = [
        "bible.txt",
        "scene_plan.txt",
        "asset_manifest.txt",
        "scene_script.txt",
        "continuity_check.txt",
        "bridge.txt",
    ]
    for name in expected:
        assert (prompts_dir / name).exists(), f"Missing prompt file: {name}"


def test_fn_stages_have_prompt_files():
    from pipelines.runner import FnStage
    registry = get_registry()
    pipeline = registry["renpy"].pipeline
    prompts_dir = Path(__file__).parent.parent / "src" / "pipelines" / "renpy" / "prompts"
    for node in pipeline.nodes:
        for stage in node.stages:
            if isinstance(stage, FnStage) and stage.prompt_file:
                assert (prompts_dir / stage.prompt_file).exists(), \
                    f"Stage {stage.id} prompt_file {stage.prompt_file!r} missing"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

SAMPLE_BIBLE = {
    "premise": "A detective must solve a murder before the killer escapes.",
    "tone_directives": [
        {"adjective": "tense", "explanation": "not brutal"},
        {"adjective": "noir", "explanation": "shadows and ambiguity"},
    ],
    "setting": {
        "name": "Rain City",
        "physical_description": "Perpetually rainy, neon-lit city.",
        "rules": "Trust no one.",
        "atmosphere": "Wet cobblestones, distant sirens.",
    },
    "characters": [
        {
            "id": "alex",
            "name": "Alex Crane",
            "role": "protagonist",
            "secret": "Alex was present the night of the crime.",
            "speech_pattern": "Clipped, dry, uses rhetorical questions.",
            "appearance": "Tall, worn coat, tired eyes.",
            "color": "#c8ffc8",
        },
        {
            "id": "mira",
            "name": "Mira Voss",
            "role": "antagonist",
            "secret": "Mira is the killer.",
            "speech_pattern": "Smooth, over-precise, never contractions.",
            "appearance": "Sharp suit, perpetual smile.",
            "color": "#c8c8ff",
        },
    ],
    "themes": ["Can justice exist without truth?"],
}


def test_bible_summary_contains_key_info():
    summary = _bible_summary(SAMPLE_BIBLE)
    assert "Alex Crane" in summary
    assert "Mira Voss" in summary
    assert "tense" in summary
    assert "Rain City" in summary
    assert "Perpetually rainy" in summary


def test_character_vars_block():
    block = _character_vars_block(SAMPLE_BIBLE)
    assert "alex" in block
    assert "Alex Crane" in block
    assert "mira" in block


def test_validate_scene_script_valid():
    script = 'label scene_001:\n    scene bg_office with dissolve\n    alex "Let\'s talk."\n    jump scene_002\n'
    valid, err = _validate_scene_script("scene_001", script)
    assert valid
    assert err == ""


def test_validate_scene_script_missing_label():
    script = '    scene bg_office\n    alex "Hello."\n    jump scene_002\n'
    valid, err = _validate_scene_script("scene_001", script)
    assert not valid
    assert "label" in err


def test_validate_scene_script_too_short():
    valid, err = _validate_scene_script("scene_001", "label scene_001:\n")
    assert not valid


def test_find_script_issues_clean():
    script = (
        'label scene_001:\n'
        '    scene bg_office with dissolve\n'
        '    show alex\n'
        '    alex "We meet again."\n'
        '    jump scene_002\n'
    )
    issues = _find_script_issues(
        script,
        valid_labels={"scene_001", "scene_002", "start"},
        valid_backgrounds={"bg_office"},
        valid_characters={"alex"},
    )
    assert issues == ""


def test_find_script_issues_detects_broken_jump():
    script = 'label scene_001:\n    alex "Hi."\n    jump scene_999\n'
    issues = _find_script_issues(
        script,
        valid_labels={"scene_001", "start"},
        valid_backgrounds=set(),
        valid_characters={"alex"},
    )
    assert "scene_999" in issues


def test_find_script_issues_detects_unknown_bg():
    script = 'label scene_001:\n    scene bg_nonexistent with dissolve\n    alex "Hi."\n    jump scene_002\n'
    issues = _find_script_issues(
        script,
        valid_labels={"scene_001", "scene_002"},
        valid_backgrounds={"bg_office"},
        valid_characters={"alex"},
    )
    assert "bg_nonexistent" in issues


def test_scenes_for_character():
    scripts = {
        "scene_001": 'label scene_001:\n    alex "Hello."\n    jump scene_002\n',
        "scene_002": 'label scene_002:\n    mira "Interesting."\n    jump scene_003\n',
        "scene_003": 'label scene_003:\n    alex "Goodbye."\n    return\n',
    }
    alex_scenes  = _scenes_for_character("alex", scripts)
    mira_scenes  = _scenes_for_character("mira", scripts)
    other_scenes = _scenes_for_character("ghost", scripts)

    assert {s["scene_id"] for s in alex_scenes}  == {"scene_001", "scene_003"}
    assert {s["scene_id"] for s in mira_scenes}  == {"scene_002"}
    assert other_scenes == []


# ---------------------------------------------------------------------------
# Stitch script
# ---------------------------------------------------------------------------

SAMPLE_MANIFEST = {
    "backgrounds": [
        {"id": "bg_office", "name": "The Office", "image_file": "office.png"},
    ],
    "characters": [
        {"id": "alex", "name": "Alex Crane", "color": "#c8ffc8", "image_file": "alex.png"},
        {"id": "mira", "name": "Mira Voss",  "color": "#c8c8ff", "image_file": "mira.png"},
    ],
}

SAMPLE_SCRIPTS = {
    "scene_001": 'label scene_001:\n    scene bg_office with dissolve\n    alex "We meet."\n    jump scene_002\n',
    "scene_002": 'label scene_002:\n    mira "We do."\n    "The End."\n    return\n',
}


# ---------------------------------------------------------------------------
# Brief validation
# ---------------------------------------------------------------------------

def test_validate_brief_passes_valid():
    _validate_brief({"genre": "romance", "tone": "tender", "setting": "Tokyo"})


def test_validate_brief_raises_on_missing_genre():
    import pytest
    with pytest.raises(ValueError, match="genre"):
        _validate_brief({"tone": "dark", "setting": "Tokyo"})


def test_validate_brief_raises_on_missing_tone():
    import pytest
    with pytest.raises(ValueError, match="tone"):
        _validate_brief({"genre": "horror", "setting": "forest"})


def test_validate_brief_raises_on_missing_setting():
    import pytest
    with pytest.raises(ValueError, match="setting"):
        _validate_brief({"genre": "sci-fi", "tone": "epic"})


def test_validate_brief_raises_on_empty_brief():
    import pytest
    with pytest.raises(ValueError):
        _validate_brief({})


# ---------------------------------------------------------------------------
# scene_script.txt prompt content
# ---------------------------------------------------------------------------

def test_scene_script_prompt_has_narrator_guidance():
    prompt_file = Path(__file__).parent.parent / "src" / "pipelines" / "renpy" / "prompts" / "scene_script.txt"
    content = prompt_file.read_text()
    assert "narration" in content.lower() or "narrator" in content.lower()


def test_scene_script_prompt_has_multiline_guidance():
    prompt_file = Path(__file__).parent.parent / "src" / "pipelines" / "renpy" / "prompts" / "scene_script.txt"
    content = prompt_file.read_text()
    assert "consecutive" in content or "alternation" in content


def test_scene_script_prompt_has_opening_hint_placeholder():
    prompt_file = Path(__file__).parent.parent / "src" / "pipelines" / "renpy" / "prompts" / "scene_script.txt"
    content = prompt_file.read_text()
    assert "{opening_hint}" in content


def test_stitch_script_structure():
    stitched = _stitch_script("Test Game", SAMPLE_BIBLE, SAMPLE_MANIFEST, SAMPLE_SCRIPTS, ["scene_001", "scene_002"])

    assert 'define alex = Character("Alex Crane"' in stitched
    assert 'define mira = Character("Mira Voss"' in stitched
    assert 'image bg_office = "images/office.png"' in stitched
    assert 'image alex:' in stitched
    assert '"images/alex.png"' in stitched
    assert "label splashscreen:" in stitched
    assert "label main_menu:" in stitched
    assert "label start:" in stitched
    assert "jump scene_001" in stitched
    assert "label scene_001:" in stitched
    assert "label scene_002:" in stitched
