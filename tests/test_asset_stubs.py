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
                   "features": [{"id": "f_altar", "size": "medium",
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
                   "features": [{"id": "f_altar", "size": "medium", "label": "Old Altar",
                                 "at": "north"}], "exits": [], "connections": []},
        "interactables": [{"id": "h_a", "label": "a", "position": {"feature": "f_altar"},
                           "action": {"type": "examine", "text": "x"}}]})
    assert "feature_old_altar" in _ids(_manifest(state), "features")
    # Rewrite the zone with a DIFFERENT feature — the derived feature stubs are recomputed, so the
    # altar's stub drops and the well's appears (deletes as well as adds).
    tools["write_place"]("zone_1", {
        "kind": "world_map",
        "layout": {"size": "small", "terrain": {"open": "grass", "blocked": "wall"},
                   "features": [{"id": "f_well", "size": "small", "label": "Stone Well",
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
        "asset_manifest": {"style": {"description": "muted ink-wash"},
                           "backgrounds": [{"id": "bg_x", "image_file": "x.png",
                                            "description": "a room"}], "characters": []},
    }
    assert "mara" in missing_stub_entities(artifact)
    assert "assets_complete" in _assets_errors(artifact)
    # Reconcile (what the check's fix and the authoring tools run) closes the gap.
    artifact["asset_manifest"] = reconcile_stubs(artifact)
    assert missing_stub_entities(artifact) == []
    assert "assets_complete" not in _assets_errors(artifact)


# ── 3b. the whole-manifest one-shot write is GONE — replaced by a slim id-only shape call plus a
#        demand-driven, one-call-per-entry description fan (the live truncation bug: a cast+location
#        heavy game's full-prose write_component("asset_manifest", ...) blew the output token cap
#        mid-JSON and the retry hit the same wall) ───────────────────────────────────────────────────

from maestro.asset_stubs import missing_asset_descriptions


def test_backgrounds_exist_fix_authors_ids_only_never_the_whole_manifest():
    from maestro.modules import assets
    from maestro.modules.module import load_prompt
    chk = assets.MODULE._check_for("backgrounds_exist")
    assert chk.prompt == "assets_backgrounds_shape.txt"
    # the id-only shape skeleton — no per-entry `description`/`image_file` prose to overflow on
    assert '"description"' not in chk.skeleton and '"image_file"' not in chk.skeleton
    assert "bg_" in chk.skeleton
    text = load_prompt(chk.prompt)
    assert "do not write descriptions" in text.lower()
    # the old one-shot repair check (a raw model-authored character id matching the cast) is gone —
    # character sprite stubs are now ALWAYS code-derived from the cast, never model-authored here
    assert assets.MODULE._check_for("character_ids") is None
    prompts_dir = Path(__file__).parent.parent / "src" / "maestro" / "prompts"
    assert not (prompts_dir / "assets_write.txt").exists()
    assert not (prompts_dir / "assets_character_ids_fix.txt").exists()


def test_missing_descriptions_fires_one_error_per_undescribed_stub():
    # Two backgrounds authored (ids only, per the new shape call) with no prose yet — the fan must
    # emit ONE error per id, each independently addressable (completing one shrinks the set).
    artifact = {"asset_manifest": {
        "style": {"description": "muted ink-wash"},
        "backgrounds": [{"id": "bg_office", "image_file": "office.png"},
                        {"id": "bg_alley", "image_file": "alley.png"}],
        "characters": [], "cgs": []}}
    missing = missing_asset_descriptions(artifact)
    assert {(e["kind"], e["id"]) for e in missing} == {
        ("backgrounds", "bg_office"), ("backgrounds", "bg_alley")}
    errs = [e for e in _assets_errors_full(artifact) if e.code == "missing_descriptions"]
    assert {e.path for e in errs} == {"bg_office", "bg_alley"}


def test_every_stub_kind_lands_undescribed_and_queues_for_describe_asset(tmp_path):
    # Stubs derive NO description from upstream fields — the old fallbacks made garbage image
    # prompts (a character's VOICE spec as their sprite prompt, an item's narrative `examine`, a
    # feature's bare label). Each lands empty and queues one describe_asset call.
    state, tools = _tools(tmp_path)
    tools["add_character"]("elias", {"name": "Elias", "voice": "measured, low cadences"})
    state.write_component("places", {"place_ids": [], "places": {}})
    tools["write_place"]("z1", {
        "kind": "town",
        "layout": {"size": "small", "terrain": {"open": "dirt", "blocked": "wall"},
                   "features": [{"id": "f_rope", "size": "medium",
                                 "label": "Coil of Hemp Rope", "at": "north"}],
                   "exits": [], "connections": []},
        "interactables": [
            {"id": "h_r", "label": "rope", "position": {"feature": "f_rope"},
             "action": {"type": "examine", "text": "rope"}},
            {"id": "h_oars", "action": {"type": "take", "item": "oars"}}]})
    tools["add_item"]("oars", {"name": "Oars", "examine": "worn smooth by Elias's grip"})
    artifact = state.load_artifact()
    queued = {(e["kind"], e["id"]) for e in missing_asset_descriptions(artifact)}
    assert ("characters", "elias") in queued
    assert ("tokens", "elias_token") in queued
    assert ("items", "oars") in queued
    assert ("features", "feature_coil_of_hemp_rope") in queued
    # the upstream junk never leaked into a description
    manifest = artifact["asset_manifest"]
    assert next(c for c in manifest["characters"] if c["id"] == "elias")["description"] == ""
    # tiles + the fixed markers keep their code-derived descriptions — never queued
    kinds = {k for k, _ in queued}
    assert "tiles" not in kinds
    assert not any(i == "marker_signpost" for _, i in queued)


def test_token_inherits_the_sprite_description_once_authored():
    # One person, one look: after the sprite is described, the next reconcile fills the token's
    # empty description from it instead of queueing a second, driftable describe call.
    artifact = {
        "characters": {"characters": [{"id": "elias", "name": "Elias"}]},
        "places": {"places": {"z1": {"kind": "town", "interactables": []}}},
        "asset_manifest": {},
    }
    artifact["asset_manifest"] = reconcile_stubs(artifact)
    sprite = next(c for c in artifact["asset_manifest"]["characters"] if c["id"] == "elias")
    sprite["description"] = "A broad-shouldered ferryman in a patched oilskin coat."
    artifact["asset_manifest"] = reconcile_stubs(artifact)
    token = next(t for t in artifact["asset_manifest"]["tokens"] if t["id"] == "elias_token")
    assert token["description"] == "A broad-shouldered ferryman in a patched oilskin coat."


def _assets_errors_full(artifact):
    from maestro.modules import assets
    return assets.MODULE.get_errors(make_ctx(make_spec(), artifact))


def test_describe_asset_round_trips_one_entry(tmp_path):
    state, tools = _tools(tmp_path)
    state.write_component("asset_manifest", {
        "backgrounds": [{"id": "bg_office", "image_file": "office.png"}],
        "characters": [], "cgs": []})
    res = tools["describe_asset"]("bg_office", "A cluttered office with a metal desk and a broken "
                                  "window blind; papers stacked on the floor.")
    assert res["ok"], res
    bg = next(b for b in _manifest(state)["backgrounds"] if b["id"] == "bg_office")
    assert "metal desk" in bg["description"]
    # the fan shrinks once the entry is described — visible progress, no false stall
    assert missing_asset_descriptions(state.load_artifact()) == []


def test_describe_asset_refuses_unknown_id_without_dumping_candidates(tmp_path):
    state, tools = _tools(tmp_path)
    state.write_component("asset_manifest", {
        "backgrounds": [{"id": "bg_office", "image_file": "office.png"}],
        "characters": [], "cgs": []})
    res = tools["describe_asset"]("bg_ghost", "a room that doesn't exist")
    assert res["ok"] is False
    assert "bg_ghost" in res["error"]
    # refuses plainly — no candidate-id dump for the model to copy-bait off of
    assert "bg_office" not in res["error"]
    # unaffected — the real entry stays undescribed, not silently matched
    bg = next(b for b in _manifest(state)["backgrounds"] if b["id"] == "bg_office")
    assert "description" not in bg


def test_describe_asset_requires_a_non_empty_description(tmp_path):
    state, tools = _tools(tmp_path)
    state.write_component("asset_manifest", {
        "backgrounds": [{"id": "bg_office", "image_file": "office.png"}], "characters": [], "cgs": []})
    assert tools["describe_asset"]("bg_office", "   ")["ok"] is False


def test_completing_one_description_shrinks_the_todo_but_not_the_others(tmp_path):
    state, tools = _tools(tmp_path)
    state.write_component("asset_manifest", {
        "backgrounds": [{"id": "bg_office", "image_file": "office.png"},
                        {"id": "bg_alley", "image_file": "alley.png"}],
        "characters": [], "cgs": []})
    before = {e["id"] for e in missing_asset_descriptions(state.load_artifact())}
    assert before == {"bg_office", "bg_alley"}
    tools["describe_asset"]("bg_office", "A cluttered office with a metal desk.")
    after = {e["id"] for e in missing_asset_descriptions(state.load_artifact())}
    assert after == {"bg_alley"}


def test_missing_description_context_grounds_background_in_its_usage_or_the_premise():
    from maestro.modules import assets
    # No node/place references it yet (backgrounds are named ahead of scenes) — falls back to premise.
    art_bare = {"asset_manifest": {"style": {"description": "muted ink-wash"},
                                   "backgrounds": [{"id": "bg_office", "image_file": "o.png"}],
                                   "characters": [], "cgs": []}}
    spec = {"concept": "a noir two-hander in a cramped office", "params": {}}
    ctx = make_ctx(spec, art_bare)
    err = next(e for e in assets.MODULE.get_errors(ctx) if e.code == "missing_descriptions")
    user = assets.MODULE.get_correction_prompt(ctx, err).user
    assert "noir two-hander" in user

    # Once a scene is set there, the context grounds the description in that concrete usage.
    art_used = {**art_bare, "nodes": {"node_ids": ["s1"], "nodes": {
        "s1": {"lines": [], "end": {"type": "end"}, "location": "bg_office"}}}}
    ctx2 = make_ctx(spec, art_used)
    err2 = next(e for e in assets.MODULE.get_errors(ctx2) if e.code == "missing_descriptions")
    user2 = assets.MODULE.get_correction_prompt(ctx2, err2).user
    assert "s1 is set here" in user2


# ── 4. styled prompt stage saves an inspectable prompt per stub ──────────────────────────────────

def test_styled_stage_saves_prompts_on_every_stub():
    from maestro.asset_prompts import apply_styled_prompts
    artifact = {
        "characters": {"characters": [{"id": "mara", "name": "Mara", "description": "a wry informant"}]},
        "nodes": {"node_ids": ["s1"], "nodes": {"s1": {
            "lines": [{"speaker": None, "text": "dim"}], "end": {"type": "end"},
            "location": "bg_office"}}},
        "story": {"spine": {"theme": "loyalty under scarcity", "tone": "wry and bleak"}},
        "asset_manifest": {"style": {"description": "muted amber lamplight, ink-wash shadows"}},
    }
    spec = {"concept": "a noir two-hander", "title": "Cold Office"}
    manifest = apply_styled_prompts(artifact, spec)
    # The brief is the in-loop AUTHORED visual language — never the concept/theme meta-prose
    # ("Art direction for A turn-based RPG where..." rendered as nothing).
    assert "muted amber lamplight" in manifest["style"]["prompt"]
    assert "noir two-hander" not in manifest["style"]["prompt"]
    assert "loyalty under scarcity" not in manifest["style"]["prompt"]
    assert manifest["style"]["description"] == "muted amber lamplight, ink-wash shadows"
    mara = next(c for c in manifest["characters"] if c["id"] == "mara")
    office = next(b for b in manifest["backgrounds"] if b["id"] == "bg_office")
    assert "a wry informant" in mara["prompt"] and "muted amber lamplight" in mara["prompt"]
    # background prompt is environmental-only (kills the named-creature bug at the source)
    assert "no people" in office["prompt"].lower() or "no characters" in office["prompt"].lower()


def test_cg_prompt_allows_characters_in_frame():
    # A cg IS a character moment — it must not ride the background template's "no people" suffix
    # (a cg describing two characters plus "no characters in frame" is a self-contradiction).
    from maestro.asset_prompts import apply_styled_prompts
    artifact = {"asset_manifest": {
        "style": {"description": "muted amber lamplight"},
        "backgrounds": [], "characters": [],
        "cgs": [{"id": "cg_final", "image_file": "cg_final.png",
                 "description": "Elias and Mara stand at the helm of the repaired ferry."}]}}
    manifest = apply_styled_prompts(artifact, {"title": "T"})
    cg = next(c for c in manifest["cgs"] if c["id"] == "cg_final")
    assert "no people" not in cg["prompt"].lower() and "no characters" not in cg["prompt"].lower()
    assert "Elias and Mara" in cg["prompt"]


def test_missing_style_fires_then_clears_via_describe_asset(tmp_path):
    from maestro.modules import assets
    state, tools = _tools(tmp_path)
    state.write_component("asset_manifest", {
        "backgrounds": [{"id": "bg_office", "image_file": "office.png", "description": "a room"}],
        "characters": [], "cgs": []})
    errs = {e.code for e in assets.MODULE.get_errors(make_ctx(make_spec(), state.load_artifact()))}
    assert "missing_style" in errs
    res = tools["describe_asset"]("style", "muted river greens, overcast light, painterly")
    assert res["ok"], res
    manifest = _manifest(state)
    assert manifest["style"]["description"].startswith("muted river greens")
    errs = {e.code for e in assets.MODULE.get_errors(make_ctx(make_spec(), state.load_artifact()))}
    assert "missing_style" not in errs


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


def test_marker_stubs_cover_every_hotspot_verb_uncapped():
    # start_combat had no marker (rendered as a bare colour chip), use hotspots had no prop, and
    # a [:12] cap silently dropped the overflow examine props — every hotspot the game authors
    # must land its marker/prop stub.
    from maestro.asset_stubs import _marker_stubs
    inter = [{"id": f"h_{i}", "label": f"thing {i}",
              "action": {"type": "examine", "text": "x"}} for i in range(15)]
    inter += [{"id": "h_fight", "label": "Ambush", "action": {"type": "start_combat",
                                                              "encounter": "e1"}},
              {"id": "h_lever", "label": "Rusty Lever",
               "action": {"type": "use", "fallback": {"text": "t"}}},
              {"id": "h_out", "label": "North", "action": {"type": "move", "target": "z2"}}]
    places = {"places": {"z1": {"kind": "town", "interactables": inter}}}
    out = _marker_stubs(places, None)
    ids = {e["id"] for e in out}
    assert "marker_combat" in ids and "marker_signpost" in ids
    assert "prop_rusty_lever" in ids                       # use hotspots get their prop
    assert sum(1 for i in ids if i.startswith("prop_thing_")) == 15   # no silent cap
