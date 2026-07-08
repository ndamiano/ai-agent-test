"""The run-state FRAME renderers in `maestro/context_render.py` — the blocks every build step's
prompt is assembled from. These had NO coverage, yet the rendered text IS the product handed to the
model: a block that silently drops the data it was given, or renders an empty header, is a prompt
bug. These tests pin what each block ACTUALLY emits — the data lands, and an empty input omits the
whole section rather than leaving a dangling header.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from conftest import make_ctx
from maestro import context_render as cr
from maestro.modules.module import Error, ErrorType


def _err(component="nodes", code="dangling_ref", message="node 'node_missing' does not exist",
         path="nodes[node_x].end.target", ref="node_missing", kind="node"):
    return Error(type=ErrorType.FIX, code=code, component=component, message=message,
                 path=path, ref=ref, kind=kind)


# A small but cross-module artifact: one id of every kind id_catalogues sweeps, so we can assert the
# catalogue actually surfaces each module's real ids (not a stubbed shape).
_ART = {
    "characters": {"characters": [{"id": "char_hero", "name": "Hero", "role": "protagonist"}]},
    "nodes": {"node_ids": ["node_intro"], "synopses": {"node_intro": "the opening"}},
    "places": {"place_ids": ["place_town"], "start_place": "place_town",
               "places": {"place_town": {"interactables": [{"id": "hs_door"}]}}},
    "items": {"items": [{"id": "item_key", "name": "Brass Key"}]},
    "asset_manifest": {"backgrounds": [{"id": "bg_town", "description": "a market town"}]},
    "combat": {"stats": [{"id": "stat_hp"}], "abilities": [{"id": "ab_slash"}]},
}


class _FakeModule:
    """A module stand-in for the block renderers — they only read `.self_digest` and `.mode_tools`."""
    component = "nodes"
    mode_tools = ("edit_node",)

    def self_digest(self, art):
        return ["", "CURRENT nodes (graph view):", "node_intro"]


# ── todo_block ────────────────────────────────────────────────────────────────

def test_todo_block_lists_each_error_with_header():
    # WHY: the to-do is the model's map of what's failing; every error must appear tagged by its
    # component + code so the model can tell them apart.
    out = cr.todo_block([_err(component="nodes", code="dangling_ref", message="bad ref"),
                         _err(component="cast", code="min_count", message="need more")])
    text = "\n".join(out)
    assert "TO-DO (failing done-conditions):" in out[0]
    assert "[nodes] dangling_ref: bad ref" in text
    assert "[cast] min_count: need more" in text


def test_todo_block_empty_shows_completion_hint_not_bare_header():
    # WHY: an empty to-do must read as "may be complete", never a header with nothing under it.
    out = cr.todo_block([])
    assert "(none — build may be complete)" in "\n".join(out)


# ── target_block ──────────────────────────────────────────────────────────────

def test_target_block_names_the_one_target():
    # WHY: the target block focuses the step on ONE check; it must carry that error's component/code/
    # message verbatim so the model clears the right thing.
    out = cr.target_block({"target": _err(component="nodes", code="dangling_ref",
                                          message="node 'node_missing' missing")})
    text = "\n".join(out)
    assert "[nodes] dangling_ref: node 'node_missing' missing" in text
    assert "Don't chase other to-do items." in text


def test_target_block_absent_is_empty():
    # WHY: no target => the section must vanish entirely (no lead-in with a blank target).
    assert cr.target_block({}) == []


# ── premise_block ─────────────────────────────────────────────────────────────

def test_premise_block_includes_concept_and_request_when_present():
    # WHY: an authoring call invents content against the premise — title + concept + request must all
    # be rendered so the model has the story it's writing into.
    out = cr.premise_block({"spec": {"title": "Neon Requiem", "concept": "a dying city",
                                     "request": "make a noir VN"}})
    text = "\n".join(out)
    assert "TITLE: Neon Requiem" in text
    assert "CONCEPT: a dying city" in text
    assert "REQUEST: make a noir VN" in text


def test_premise_block_omits_missing_optional_fields():
    # WHY: concept/request are optional; when absent the block must not emit empty "CONCEPT:"/
    # "REQUEST:" lines. Title always renders (even if blank) as the anchor.
    out = cr.premise_block({"spec": {"title": "Solo"}})
    text = "\n".join(out)
    assert "TITLE: Solo" in text
    assert "CONCEPT:" not in text
    assert "REQUEST:" not in text


# ── story_tail_block ──────────────────────────────────────────────────────────

def test_story_tail_block_renders_recent_events_and_threads():
    # WHY: the LIVE continuity an authoring call must respect — the recent tail and open threads must
    # both surface so a new scene doesn't contradict what just happened.
    out = cr.story_tail_block({"story_state": {"recent_events_tail": ["the bridge fell"],
                                               "open_threads": ["who cut the rope?"]}})
    text = "\n".join(out)
    assert "STORY SO FAR (most recent):" in text
    assert "the bridge fell" in text
    assert "OPEN THREADS:" in text
    assert "who cut the rope?" in text


def test_story_tail_block_empty_state_is_empty():
    # WHY: no story state => no section (the block guards on an empty dict).
    assert cr.story_tail_block({"story_state": {}}) == []
    assert cr.story_tail_block({}) == []


# ── tail_block ────────────────────────────────────────────────────────────────

def test_tail_block_shows_last_read_and_result():
    # WHY: every step trails the last read payload + last tool result so the model sees what it just
    # did without re-reading.
    out = cr.tail_block({"last_read": "node_intro contents", "last_result": "wrote node_intro"})
    text = "\n".join(out)
    assert "LAST READ:\nnode_intro contents" in text
    assert "LAST RESULT: wrote node_intro" in text


def test_tail_block_stall_nudge_only_when_stalled():
    # WHY: the anti-thrash nudge must appear ONLY when the loop flags a repeated no-op read — it's a
    # behaviour override, not decoration.
    stalled = "\n".join(cr.tail_block({"last_result": "re-read", "stalled": True}))
    calm = "\n".join(cr.tail_block({"last_result": "wrote node_intro"}))
    assert "STOP reading" in stalled
    assert "STOP reading" not in calm


# ── self_digest_block ─────────────────────────────────────────────────────────

def test_self_digest_block_delegates_to_module():
    # WHY: the block is a thin delegate to the module's compact self-view — it must pass the artifact
    # through and return exactly what the module produced.
    out = cr.self_digest_block(_FakeModule(), {"artifact": _ART})
    assert out == ["", "CURRENT nodes (graph view):", "node_intro"]


# ── id_catalogues ─────────────────────────────────────────────────────────────

def test_id_catalogues_surfaces_every_kind_of_id_in_the_artifact():
    # WHY: a crossref/compile fix repoints against these EXACT ids — every realizable id in the
    # artifact (character/node/place/item/background/combat) must appear so the fix has a real target.
    text = "\n".join(cr.id_catalogues(_ART))
    for real_id in ("char_hero", "node_intro", "place_town", "item_key", "bg_town", "stat_hp"):
        assert real_id in text, f"{real_id} missing from id catalogues"


def test_id_catalogues_empty_artifact_is_empty():
    # WHY: with no content there are no ids to list — every index guards on emptiness, so the whole
    # catalogue collapses to nothing rather than a stack of empty headers.
    assert cr.id_catalogues({}) == []


# ── ctx_structural / ctx_crossref archetypes ──────────────────────────────────

def test_ctx_structural_is_target_plus_selfview_plus_tail_no_catalogues():
    # WHY: a structural repair on the module's OWN component gets the target + its compact self-view +
    # run-state — and deliberately NOT the cross-module id catalogues (a dedup/field fix needs no
    # foreign ids).
    rd = {"target": _err(code="distinct_nodes", message="duplicate node id"),
          "artifact": _ART, "last_result": "read node_intro"}
    text = cr.ctx_structural(_FakeModule(), rd)
    assert "distinct_nodes: duplicate node id" in text
    assert "CURRENT nodes (graph view):" in text
    assert "Make the one change that clears the target." in text
    # a foreign id (from another module) must NOT be dragged in by a structural fix
    assert "item_key" not in text


def test_ctx_crossref_carries_the_id_catalogues_and_repoint_instruction():
    # WHY: a dangling-reference fix needs the valid-id catalogues to repoint into, plus the explicit
    # repoint instruction — that's the whole point of the crossref archetype.
    rd = {"target": _err(), "artifact": _ART, "last_result": "compile failed"}
    text = cr.ctx_crossref(_FakeModule(), rd)
    assert "node_intro" in text          # a real id to repoint to
    assert "char_hero" in text           # the full catalogue is offered
    assert "Repoint the reference to a real id above" in text


# ── _kind_catalogue ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,expect_present,expect_absent", [
    ("node", "node_intro", "char_hero"),      # node kind => ONLY the node index
    ("character", "char_hero", "node_intro"),  # character kind => ONLY the character index
    ("item", "item_key", "place_town"),
])
def test_kind_catalogue_scopes_to_the_failing_kind(kind, expect_present, expect_absent):
    # WHY: a per-kind crossref fix ships ONLY the catalogue the failed ref resolves into (no generic
    # whole-catalogue dump) — so a node-ref fix sees nodes, not characters.
    text = "\n".join(cr._kind_catalogue(kind)(_ART))
    assert expect_present in text
    assert expect_absent not in text


def test_kind_catalogue_unknown_kind_falls_back_to_full_catalogues():
    # WHY: an unrecognised kind has no scoped index, so it degrades to the full id_catalogues rather
    # than emitting nothing (a fix with no ids can't repoint).
    text = "\n".join(cr._kind_catalogue("mystery")(_ART))
    assert "node_intro" in text and "char_hero" in text


# ── crossref_correction ───────────────────────────────────────────────────────

def test_crossref_correction_embeds_the_dangling_ref_and_scopes_tools_and_catalogue():
    # WHY: the built correction prompt is what the model actually receives — it must embed the
    # specific dangling ref (via the target message), offer the node-kind catalogue, and scope the
    # allowed tools to the slice that HOLDS the ref (nodes => read/edit/write_node).
    ctx = make_ctx({"title": "T", "params": {}}, _ART)
    err = _err(path="nodes[node_x].end.target", kind="node",
               message="node 'node_missing' does not exist")
    cp = cr.crossref_correction(_FakeModule(), ctx, err)

    assert "node_missing" in cp.user                 # the specific bad ref reached the prompt
    assert "node_intro" in cp.user                   # the node-kind catalogue to repoint into
    assert "char_hero" not in cp.user                # not the character catalogue (kind-scoped)
    assert cp.allowed_tools == ("read_node", "edit_node", "write_node")
    assert cp.system.strip()                         # a real load-bearing system prompt loaded


def test_crossref_correction_unknown_kind_uses_generic_prompt_and_full_catalogue():
    # WHY: an error with no per-kind prompt falls back to the generic crossref prompt + the full id
    # catalogue, so the fix still gets a system prompt and ids to repoint into.
    ctx = make_ctx({"title": "T", "params": {}}, _ART)
    err = _err(path="nodes[node_x].end.target", kind=None,
               message="reference 'x_missing' does not resolve")
    cp = cr.crossref_correction(_FakeModule(), ctx, err)
    assert "x_missing" in cp.user
    assert "node_intro" in cp.user and "char_hero" in cp.user  # full catalogue, not kind-scoped
    assert cp.system.strip()
