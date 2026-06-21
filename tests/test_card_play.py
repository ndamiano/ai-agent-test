import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonschema
import maestro.discrete  # noqa: F401 — registers modules + presets
from maestro.modules import PRESETS, modules_for, unprojectable
from maestro.ir_assemble import assemble_ir
from maestro.ir_crossref import crossref_errors
from maestro.discrete.card_play import v_matches
from maestro import spec_tools

_SCHEMA = json.loads((Path(__file__).parent.parent / "docs" / "game_ir.schema.json").read_text())
_VALIDATOR = jsonschema.Draft202012Validator({k: v for k, v in _SCHEMA.items() if k != "examples"})


def _card_artifact():
    return {
        "spec": {"genre": "card_ante"},
        "premise": {"central_question": "Can you out-bluff the house?",
                    "characters": [{"id": "gambler", "name": "The Gambler"}]},
        "asset_manifest": {"backgrounds": [{"id": "bg_saloon", "image_file": "saloon.png"}],
                           "characters": [{"id": "gambler", "image_file": "gambler.png"}], "cgs": []},
        "nodes": {"node_ids": ["greet"], "nodes": {
            "greet": {"lines": [{"speaker": "gambler", "text": "Howdy, stranger."},
                                {"speaker": "gambler", "text": "Care to play?"},
                                {"speaker": None, "text": "He shuffles."}],
                      "end": {"type": "return"}}}},
        "places": {"start_place": "saloon", "place_ids": ["saloon"],
                   "variables": [{"id": "gold", "default": 100}], "flags": ["won"],
                   "goal": {"type": "flag", "id": "won"},
                   "places": {"saloon": {"kind": "room", "background": "bg_saloon", "interactables": [
                       {"id": "h_table", "label": "card table",
                        "position": {"rect": {"x": 1, "y": 1, "w": 1, "h": 1}},
                        "action": {"type": "play_match", "match": "match_gambler"}},
                       {"id": "h_gambler", "label": "the gambler",
                        "position": {"rect": {"x": 2, "y": 2, "w": 1, "h": 1}},
                        "action": {"type": "talk", "node": "greet"}}]}}},
        "matches": {"match_ids": ["match_gambler"], "matches": {
            "match_gambler": {"card_model": "blackjack", "deck_model": "standard_52",
                              "opponent": "gambler", "ante": {"var": "gold", "amount": 50},
                              "rounds": 1,
                              "on_win": {"effects": [{"add_var": {"var": "gold", "delta": 50}},
                                                     {"set_flag": "won"}], "end": {"type": "return"}},
                              "on_lose": {"effects": [{"add_var": {"var": "gold", "delta": -50}}],
                                          "end": {"type": "return"}}}}},
    }


# ── preset + classifier ───────────────────────────────────────────────────────

def test_card_ante_preset_composition():
    p = PRESETS["card_ante"]
    assert p.engine == "web"
    assert "card_play" in p.modules and "navigation" in p.modules
    assert modules_for({"genre": "card_ante"}) == p.modules


def test_classifier_routes_card_requests():
    assert spec_tools._classify_genre("a card game where you wander and play for ante") == "card_ante"
    assert spec_tools._classify_genre("a blackjack hall crawl") == "card_ante"
    assert spec_tools._classify_genre("a quiet visual novel about grief") == "vn"


# ── matches validator ─────────────────────────────────────────────────────────

def test_matches_validator():
    good = _card_artifact()["matches"]
    assert v_matches(good) is None
    assert "card_model" in v_matches({"match_ids": ["m"], "matches": {"m": {"opponent": "g", "ante": {"var": "gold", "amount": 1}}}})
    assert "opponent" in v_matches({"match_ids": ["m"], "matches": {"m": {"card_model": "blackjack", "ante": {"var": "gold", "amount": 1}}}})
    assert "ante" in v_matches({"match_ids": ["m"], "matches": {"m": {"card_model": "high_card", "opponent": "g"}}})


# ── assemble + crossref ───────────────────────────────────────────────────────

def test_assemble_lifts_card_matches_and_is_clean():
    ir = assemble_ir(_card_artifact(), "card_ante")
    assert ir["genre"] == "point_and_click"             # navigation overworld
    assert [m["id"] for m in ir["card_matches"]] == ["match_gambler"]
    _VALIDATOR.validate(ir)                              # schema-valid
    assert crossref_errors(ir) == []                    # every ref resolves


def test_crossref_catches_dangling_opponent():
    art = _card_artifact()
    art["matches"]["matches"]["match_gambler"]["opponent"] = "ghost"
    errs = crossref_errors(assemble_ir(art, "card_ante"))
    assert any("ghost" in e and "character" in e for e in errs)


def test_crossref_catches_dangling_ante_var_and_play_match():
    art = _card_artifact()
    art["matches"]["matches"]["match_gambler"]["ante"]["var"] = "nope"
    art["places"]["places"]["saloon"]["interactables"][0]["action"]["match"] = "missing_match"
    errs = crossref_errors(assemble_ir(art, "card_ante"))
    assert any("nope" in e and "variable" in e for e in errs)
    assert any("missing_match" in e and "card_match" in e for e in errs)


# ── seam #1: web renders card_play, renpy does not ────────────────────────────

def test_card_play_projectable_on_web_not_renpy():
    from renpy.projections import register as reg_renpy
    from web.projections import register as reg_web
    reg_renpy()
    reg_web()
    mods = PRESETS["card_ante"].modules
    assert unprojectable("web", mods) == []              # web renders it
    assert unprojectable("renpy", mods) == ["card_play"]  # renpy cannot


def test_renpy_compile_fails_fast_on_card_game(tmp_path):
    art = _card_artifact()
    for stem, content in art.items():
        (tmp_path / f"{stem}.json").write_text(json.dumps(content), encoding="utf-8")
    from renpy.ir_compiler import compile_ir
    res = compile_ir(tmp_path, distribute=False)
    assert res["ok"] is False
    assert "card_play" in res["reason"] and "projection" in res["reason"]


# ── end-to-end: web build writes a playable card game ─────────────────────────

def test_compiles_check_dispatches_on_engine(tmp_path):
    # The `compiles` done-condition must use the spec's engine, not hardcode Ren'Py — a card game
    # builds on web; lint-checking it with the Ren'Py SDK would fail it forever.
    from maestro.validate import _check_compiles
    for stem, content in _card_artifact().items():
        (tmp_path / f"{stem}.json").write_text(json.dumps(content), encoding="utf-8")

    (tmp_path / "spec.json").write_text(json.dumps({"genre": "card_ante", "engine": "web"}))
    ok, _ = _check_compiles({}, {}, tmp_path)
    assert ok is True

    (tmp_path / "spec.json").write_text(json.dumps({"genre": "card_ante", "engine": "renpy"}))
    ok, detail = _check_compiles({}, {}, tmp_path)
    assert ok is False and "card_play" in detail


def test_web_compile_writes_card_matches(tmp_path):
    art = _card_artifact()
    for stem, content in art.items():
        (tmp_path / f"{stem}.json").write_text(json.dumps(content), encoding="utf-8")
    from web.ir_compiler import compile_ir
    res = compile_ir(tmp_path, distribute=False)
    assert res["ok"] is True, res.get("reason")
    game = json.loads((Path(res["project_dir"]) / "game.json").read_text())
    assert [m["id"] for m in game["card_matches"]] == ["match_gambler"]
    assert game["card_matches"][0]["card_model"] == "blackjack"
