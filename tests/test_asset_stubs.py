"""Asset completeness by construction — stubs emitted at authoring time, a done-condition that
proves coverage, a styled prompt stage saved on the manifest, and generation consuming only those
saved prompts. These are the contracts the asset pipeline redesign guarantees; they test behaviour
(a manifest entry exists / a prompt is saved / a job carries it), not the reconcile internals."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from maestro.state import RunState
from maestro.tools import build_tools
from maestro.asset_stubs import reconcile_stubs, missing_stub_entities
from conftest import make_spec, make_ctx


def _tools(tmp_path):
    state = RunState(tmp_path)
    return state, build_tools(make_spec(), state)


def _manifest(state) -> dict:
    return state.read_component("asset_manifest") or {}


def _ids(manifest, key) -> set:
    return {e.get("id") for e in manifest.get(key) or []}


# ── 1. every authoring tool emits a stub at creation ─────────────────────────────────────────────

def test_add_character_emits_sprite_stub(tmp_path):
    state, tools = _tools(tmp_path)
    assert tools["add_character"]("mara", {"name": "Mara", "description": "a wry informant"})["ok"]
    chars = {c["id"]: c for c in _manifest(state).get("characters", [])}
    assert "mara" in chars                                   # sprite stub emitted
    assert chars["mara"]["image_file"] == "mara.png"


def test_write_node_location_emits_background_stub(tmp_path):
    state, tools = _tools(tmp_path)
    res = tools["write_node"]("scene_1", {
        "lines": [{"speaker": None, "text": "The office is dim."}],
        "end": {"type": "end"}, "location": "bg_office"})
    assert res["ok"]
    assert "bg_office" in _ids(_manifest(state), "backgrounds")   # a referenced location is covered


@pytest.mark.parametrize("list_key,stub_id", [
    ("features", "feature_old_altar"),   # one object sprite per stamped footprint
    ("tiles", "tile_flagstone"),         # one terrain texture per distinct theme
    ("tokens", "hero_token"),            # walkable maps draw people as chibi tokens
])
def test_write_place_walkable_emits_map_stubs(tmp_path, list_key, stub_id):
    state, tools = _tools(tmp_path)
    tools["add_character"]("hero", {"name": "Hero"})
    res = tools["write_place"]("zone_1", {
        "kind": "world_map",
        "layout": {"size": "small",
                   "terrain": {"open": "flagstone", "blocked": "wall"},
                   "features": [{"id": "f_altar", "kind": "building",
                                 "label": "Old Altar", "at": "north"}],
                   "exits": [], "connections": []},
        "interactables": [{"id": "h_altar", "label": "altar", "position": {"feature": "f_altar"},
                           "action": {"type": "examine", "text": "an old altar"}}]})
    assert res["ok"], res
    assert stub_id in _ids(_manifest(state), list_key)


def test_add_item_emits_icon_stub(tmp_path):
    state, tools = _tools(tmp_path)
    # An item is authored only where a reference demands it — seed a take hotspot naming it first.
    state.write_component("places", {"place_ids": ["r1"], "places": {"r1": {
        "kind": "room", "background": "bg_room",
        "interactables": [{"id": "h_key", "action": {"type": "take", "item": "item_key"}}]}}})
    assert tools["add_item"]("item_key", {"name": "key", "examine": "a rusty iron key"})["ok"]
    icons = {i["id"]: i for i in _manifest(state).get("items", [])}
    assert "item_key" in icons and icons["item_key"]["image_file"] == "item_key.png"


# ── 2. content edits/deletes update the manifest ─────────────────────────────────────────────────

def test_removing_a_feature_drops_its_stub(tmp_path):
    state, tools = _tools(tmp_path)
    tools["write_place"]("zone_1", {
        "kind": "world_map",
        "layout": {"size": "small", "terrain": {"open": "grass", "blocked": "wall"},
                   "features": [{"id": "f_altar", "kind": "building", "label": "Old Altar",
                                 "at": "north"}], "exits": [], "connections": []},
        "interactables": [{"id": "h_a", "label": "a", "position": {"feature": "f_altar"},
                           "action": {"type": "examine", "text": "x"}}]})
    assert "feature_old_altar" in _ids(_manifest(state), "features")
    # Rewrite the zone with a DIFFERENT feature — the derived feature stubs are recomputed, so the
    # altar's stub drops and the well's appears (deletes as well as adds).
    tools["write_place"]("zone_1", {
        "kind": "world_map",
        "layout": {"size": "small", "terrain": {"open": "grass", "blocked": "wall"},
                   "features": [{"id": "f_well", "kind": "fountain", "label": "Stone Well",
                                 "at": "center"}], "exits": [], "connections": []},
        "interactables": [{"id": "h_a", "label": "a", "position": {"feature": "f_well"},
                           "action": {"type": "examine", "text": "x"}}]})
    feats = _ids(_manifest(state), "features")
    assert "feature_old_altar" not in feats and "feature_stone_well" in feats


# ── 3. the completeness done-condition fires on a missing stub, clears after reconcile ───────────

def _assets_errors(artifact):
    from maestro.modules import assets
    ctx = make_ctx(make_spec(), artifact)
    return {e.code for e in assets.MODULE.get_errors(ctx)}


def test_completeness_check_fires_then_clears():
    # A cast member with a background present but NO manifest sprite stub — an entity that would
    # render as nothing. The earlier assets checks pass, so the terminal completeness check fires.
    artifact = {
        "characters": {"characters": [{"id": "mara", "name": "Mara"}]},
        "asset_manifest": {"backgrounds": [{"id": "bg_x", "image_file": "x.png",
                                            "description": "a room"}], "characters": []},
    }
    assert "mara" in missing_stub_entities(artifact)
    assert "assets_complete" in _assets_errors(artifact)
    # Reconcile (what the check's fix and the authoring tools run) closes the gap.
    artifact["asset_manifest"] = reconcile_stubs(artifact)
    assert missing_stub_entities(artifact) == []
    assert "assets_complete" not in _assets_errors(artifact)


# ── 4. styled prompt stage saves an inspectable prompt per stub ──────────────────────────────────

def test_styled_stage_saves_prompts_on_every_stub():
    from maestro.asset_prompts import apply_styled_prompts
    artifact = {
        "characters": {"characters": [{"id": "mara", "name": "Mara", "description": "a wry informant"}]},
        "nodes": {"node_ids": ["s1"], "nodes": {"s1": {
            "lines": [{"speaker": None, "text": "dim"}], "end": {"type": "end"},
            "location": "bg_office"}}},
        "story": {"spine": {"theme": "loyalty under scarcity", "tone": "wry and bleak"}},
    }
    spec = {"concept": "a noir two-hander", "title": "Cold Office"}
    manifest = apply_styled_prompts(artifact, spec)
    # A shared style brief + a per-stub prompt that carries the game's tone.
    assert "loyalty under scarcity" in manifest["style"]["prompt"]
    mara = next(c for c in manifest["characters"] if c["id"] == "mara")
    office = next(b for b in manifest["backgrounds"] if b["id"] == "bg_office")
    assert "a wry informant" in mara["prompt"] and "wry and bleak" in mara["prompt"]
    # background prompt is environmental-only (kills the named-creature bug at the source)
    assert "no people" in office["prompt"].lower() or "no characters" in office["prompt"].lower()


# ── 5. generation consumes ONLY the saved prompt (no at-gen re-derivation) ───────────────────────

def test_generate_images_consumes_saved_prompt(tmp_path, monkeypatch):
    import contextlib
    import tools.comfyui_tools as ct
    from renpy.fns import generate_images

    monkeypatch.setattr(ct, "vram_bracket", contextlib.nullcontext)
    queued = []

    def fake_run_jobs(jobs):
        queued.extend(jobs)
        return [{"success": False, "error": "no comfyui"} for _ in jobs]

    monkeypatch.setattr(ct, "run_jobs", fake_run_jobs)

    inputs = {"characters": {"characters": [{"id": "mara", "name": "Mara"}]},
              "asset_manifest": {"characters": [
                  {"id": "mara", "image_file": "mara.png",
                   "description": "raw subject that must NOT be used",
                   "prompt": "STYLED PORTRAIT of Mara in candlelit noir"}]}}
    generate_images(inputs, tmp_path)

    mara_job = next(j for j in queued if "mara" in j["prompt"].lower())
    assert "STYLED PORTRAIT" in mara_job["prompt"]              # the saved prompt drove the job
    assert "must NOT be used" not in mara_job["prompt"]         # not the raw description
