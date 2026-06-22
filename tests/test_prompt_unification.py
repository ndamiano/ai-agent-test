"""Tests for the prompt/code DRY unification: the {{include:…}} partial mechanism, the
module-composed tool/prompt gating, and the single generated spec prompt."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.templating import render_template, _resolve_includes

_PROMPTS = Path(__file__).parent.parent / "src" / "maestro" / "prompts"


# ── {{include:NAME}} resolution ──────────────────────────────────────────────

def test_include_inlines_partial(tmp_path):
    (tmp_path / "partials").mkdir()
    (tmp_path / "partials" / "hi.txt").write_text("HELLO")
    (tmp_path / "t.txt").write_text("before {{include:hi}} after")
    assert render_template(tmp_path / "t.txt", {}) == "before HELLO after"


def test_include_is_recursive(tmp_path):
    (tmp_path / "partials").mkdir()
    (tmp_path / "partials" / "a.txt").write_text("A[{{include:b}}]")
    (tmp_path / "partials" / "b.txt").write_text("B")
    (tmp_path / "t.txt").write_text("{{include:a}}")
    assert render_template(tmp_path / "t.txt", {}) == "A[B]"


def test_missing_partial_raises(tmp_path):
    (tmp_path / "partials").mkdir()
    with pytest.raises(FileNotFoundError):
        _resolve_includes("{{include:nope}}", tmp_path / "partials")


def test_circular_include_raises(tmp_path):
    (tmp_path / "partials").mkdir()
    (tmp_path / "partials" / "a.txt").write_text("{{include:b}}")
    (tmp_path / "partials" / "b.txt").write_text("{{include:a}}")
    with pytest.raises(ValueError):
        _resolve_includes("{{include:a}}", tmp_path / "partials")


def test_include_leaves_single_brace_keys_alone(tmp_path):
    # The include pass uses double braces; the {key} substitution pass owns single braces.
    (tmp_path / "partials").mkdir()
    (tmp_path / "partials" / "p.txt").write_text("P")
    (tmp_path / "t.txt").write_text("{{include:p}} {name}")
    assert render_template(tmp_path / "t.txt", {"name": "Zed"}) == "P Zed"


def test_authoring_prompts_resolve_with_no_leftover_tokens():
    # Every build prompt that uses partials must render clean (no stray include/key tokens).
    for name in ["write_node.txt", "write_place.txt", "fix_node.txt", "fix_place.txt",
                 "mode_premise.txt", "mode_matches.txt", "mode_asset.txt",
                 "build_agent_system.txt"]:
        out = render_template(_PROMPTS / name, {})
        assert "{{include" not in out and "{include" not in out, name


def test_tool_call_rule_partial_shared_verbatim():
    # The "you write JSON not Ren'Py" rule is one source now — present in every IR-authoring prompt.
    rule = (_PROMPTS / "partials" / "tool_call_rule.txt").read_text().strip()
    for name in ["write_node.txt", "write_place.txt", "fix_node.txt", "fix_place.txt"]:
        assert rule in render_template(_PROMPTS / name, {}), name


# ── module-composed gating (§10) ─────────────────────────────────────────────

def test_node_gating_lives_on_dialogue_module():
    from maestro.discrete.dialogue import SPINE, NPC
    # Both configs own `nodes` and carry the same sub-loop gating (shared constant).
    for m in (SPINE, NPC):
        assert "write_node" in m.mode_tools and "write_component" not in m.mode_tools
        assert m.prompts == {"author": "write_node.txt", "fix": "fix_node.txt"}
        assert m.target_jobs["count"] == "author" and m.target_jobs["compiles"] == "fix"
        assert m.target_tools["count"] == frozenset({"write_node"})
        assert m.subloop["count_tool"] == "write_node"


def test_compose_exposes_mode_and_subloop_maps():
    from maestro.modules import compose, PRESETS
    c = compose(PRESETS["vn"].modules)
    assert set(c.mode_prompts) == {"premise", "asset_manifest", "outline", "nodes"}
    assert c.mode_prompts["premise"] == "mode_premise.txt"
    assert c.mode_prompts["outline"] == "mode_outline.txt"
    assert c.mode_prompts["nodes"] == "write_node.txt"
    assert list(c.subloop_modules) == ["nodes"]          # only the dialogue body sub-loops
    assert "write_node" in c.mode_tools["nodes"]


def test_pnc_composition_subloops_places():
    from maestro.modules import compose, PRESETS
    c = compose(PRESETS["point_and_click"].modules)
    assert "places" in c.subloop_modules
    assert c.mode_prompts["places"] == "write_place.txt"
    assert "write_place" in c.mode_tools["places"]


# ── single generated spec prompt (§9) ────────────────────────────────────────

def test_spec_prompt_is_generated_per_genre():
    from maestro.spec_tools import _spec_prompt_ctx
    for genre in ["vn", "point_and_click", "card_ante"]:
        prompt = render_template(_PROMPTS / "propose_spec.txt", _spec_prompt_ctx("make a thing", genre))
        assert "{{include" not in prompt and "{request}" not in prompt
        assert "allowed done-condition" in prompt          # the shared allowed_checks partial
        assert "central_question" in prompt                # the composed skeletons/baseline


def test_spec_prompt_embeds_composed_baseline():
    # The default-condition set is rendered FROM the modules' baseline (one source), so the
    # VN floor checks must appear in the prompt body, not be re-hardcoded in the template.
    from maestro.spec_tools import _spec_prompt_ctx
    prompt = render_template(_PROMPTS / "propose_spec.txt", _spec_prompt_ctx("a VN", "vn"))
    for check in ["reachable_from_start", "all_characters_speak", "min_branches"]:
        assert check in prompt


def test_old_per_genre_spec_prompts_are_gone():
    assert not (_PROMPTS / "propose_spec_pnc.txt").exists()
    assert not (_PROMPTS / "propose_spec_card.txt").exists()
