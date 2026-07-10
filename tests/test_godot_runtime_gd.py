"""GDScript-level self-test for the Godot runtime's pure core (ir.gd).

Skipped unless a `godot` binary is on PATH (same spirit as tests/integration). When present, it
copies the runtime to a temp project, drops a SceneTree self-test beside it, and runs it headless —
catching GDScript parse errors and IRCore logic regressions the Python tests can't see.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).parent.parent / "src" / "godot" / "runtime"
_GODOT = shutil.which("godot") or shutil.which("godot4")

_SELFTEST = """
extends SceneTree

const IRCore = preload("res://ir.gd")

func _fail(msg):
\tprint("SELFTEST FAIL: ", msg)
\tquit(1)

func _initialize():
\tvar ir = {"flags": ["a"], "variables": [{"id": "g", "default": 5}]}
\tvar st = IRCore.make_state(ir)
\tif st["vars"]["g"] != 5: _fail("default var"); return
\tIRCore.apply_effect(st, {"set_flag": "a"})
\tif not IRCore.eval_cond(st, {"flag": "a"}): _fail("set_flag"); return
\tif not IRCore.eval_cond(st, {"var": "g", "op": ">=", "value": 5}): _fail("var cmp"); return
\tif IRCore.eval_cond(st, {"not": {"flag": "a"}}): _fail("not"); return
\tIRCore.apply_effects(st, [{"add_var": {"var": "g", "delta": -2}}, {"add_item": "key"}])
\tif st["vars"]["g"] != 3: _fail("add_var"); return
\tif not IRCore.eval_cond(st, {"item": "key"}): _fail("add_item"); return
\tif not IRCore.eval_cond(st, {"all": [{"flag": "a"}, {"item": "key"}]}): _fail("all"); return
\tprint("SELFTEST OK")
\tquit(0)
"""


# Drives combat.gd's turn loop with a stub Game driver (all awaits resolve synchronously):
# a DoT wipe under unmatched when-conditions must resolve instead of spinning forever, a poison
# kill at the victim's turn start must end combat before the enemy acts again, and vn.menu_pick
# (the path Game._flow routes a combat menu end through) must filter/apply/return correctly.
_COMBAT_SELFTEST = """
extends SceneTree

const Combat = preload("res://combat.gd")
const Vn = preload("res://vn.gd")

class FakeGame:
\tvar state = {"flags": {}, "vars": {}, "inv": []}
\tvar chars = {}
\tvar node_by_id = {}
\tvar stat_by_id = {}
\tvar ability_by_id = {}
\tvar status_by_id = {}
\tvar combatant_by_id = {}
\tvar encounter_by_id = {}
\tvar lines = []
\tvar ir = {}
\tfunc pstats(): return null
\tfunc set_scene(_bg): pass
\tfunc show_line(_sp, text): lines.append(text)
\tfunc hide_dialogue(): pass
\tfunc set_hud(_t): pass
\tfunc show_menu(_labels): return 0

func _fail(msg):
\tprint("SELFTEST FAIL: ", msg)
\tquit(1)

func _make_game() -> FakeGame:
\tvar g = FakeGame.new()
\tg.stat_by_id = {"hp": {"id": "hp", "default": 5, "role": "resource_depletable",
\t\t"min": 0, "max": 5}}
\tg.status_by_id = {"poison": {"id": "poison",
\t\t"tick": [{"stat": "hp", "op": "damage", "formula": {"base": 99}}]}}
\tg.ability_by_id = {
\t\t"curse": {"id": "curse", "name": "Curse", "targeting": {"shape": "self"},
\t\t\t"effects": [{"status": "poison", "duration": 3}]},
\t\t"hit": {"id": "hit", "name": "Hit",
\t\t\t"targeting": {"shape": "single", "faction": "enemy"},
\t\t\t"effects": [{"stat": "hp", "op": "damage", "formula": {"base": 1}}]}}
\tg.combatant_by_id = {
\t\t"hero": {"id": "hero", "abilities": ["curse"]},
\t\t"goblin": {"id": "goblin", "abilities": ["hit"]}}
\treturn g

func _initialize():
\t_run()

func _run():
\tvar g = _make_game()
\tg.encounter_by_id = {"e1": {"id": "e1",
\t\t"combatants": [{"ref": "hero", "faction": "player"}],
\t\t"victory": {"when": {"flag": "nope"}}, "defeat": {"when": {"flag": "nope"}},
\t\t"on_defeat": {"type": "end", "ending": "wiped"}}}
\tvar res = await Combat.new(g).run("e1")
\tif res == null or res.get("ending") != "wiped": _fail("dead field did not resolve"); return

\tg = _make_game()
\tg.encounter_by_id = {"e2": {"id": "e2",
\t\t"combatants": [{"ref": "hero", "faction": "player"}, {"ref": "goblin", "faction": "enemy"}],
\t\t"victory": {"all_defeated": "enemy"}}}
\tres = await Combat.new(g).run("e2")
\tif res == null or res.get("ending") != "game_over": _fail("default defeat not returned"); return
\tvar goblin_acts = 0
\tfor l in g.lines:
\t\tif l.begins_with("goblin uses"):
\t\t\tgoblin_acts += 1
\tif goblin_acts != 1: _fail("enemy acted %d times, not 1" % goblin_acts); return

\tg = _make_game()
\tvar target = await Vn.new(g).menu_pick({"type": "menu", "choices": [
\t\t{"text": "locked", "requires": {"flag": "nope"}, "target": "n_locked"},
\t\t{"text": "go", "target": "n_go", "effects": [{"set_flag": "went"}]}]})
\tif target != "n_go": _fail("menu_pick target"); return
\tif not g.state["flags"].get("went", false): _fail("menu_pick effects"); return

\tprint("SELFTEST OK")
\tquit(0)
"""


# The overworld snaps a bad/absent arrival cell onto walkable ground: New Game enters the start
# place with no spawn, defaulting the avatar to (0,0) — a bordered map's wall corner boxed in by
# more wall. Without the snap the player can never take a step.
_OVERWORLD_SELFTEST = """
extends SceneTree

const Overworld = preload("res://overworld.gd")

class FakeGame:
\tvar _x = 0

func _fail(msg):
\tprint("SELFTEST FAIL: ", msg)
\tquit(1)

func _initialize():
\tvar ov = Overworld.new(FakeGame.new())
\t# 3x3 ringed by wall; only the centre (1,1) is open.
\tvar blocked = {}
\tfor c in ["0,0", "1,0", "2,0", "0,1", "2,1", "0,2", "1,2", "2,2"]:
\t\tblocked[c] = true
\tif ov._nearest_open(0, 0, blocked, 3, 3) != Vector2i(1, 1):
\t\t_fail("null-spawn corner not snapped to open centre"); return
\tif ov._nearest_open(1, 1, blocked, 3, 3) != Vector2i(1, 1):
\t\t_fail("an already-open cell must be returned unchanged"); return
\tprint("SELFTEST OK")
\tquit(0)
"""


# The runtime parses the PCM WAV the music pipeline writes into an AudioStreamWAV by hand (Godot
# 4.2 has no runtime WAV-from-buffer loader). Feed it a real stub pad + a silent placeholder and
# assert the parse (format/rate/loop) + the missing-file → null (silent) degrade.
_MUSIC_SELFTEST = """
extends SceneTree

func _fail(msg):
\tprint("SELFTEST FAIL: ", msg)
\tquit(1)

func _initialize():
\tvar g = load("res://Game.gd").new()
\tvar s = g._load_wav("res://audio/music/music_main.wav")
\tif s == null: _fail("stub pad not parsed"); return
\tif s.format != AudioStreamWAV.FORMAT_16_BITS: _fail("format"); return
\tif s.mix_rate != 22050: _fail("mix_rate %d" % s.mix_rate); return
\tif s.loop_mode != AudioStreamWAV.LOOP_FORWARD: _fail("loop_mode"); return
\tif s.data.size() == 0: _fail("no pcm data"); return
\tif g._load_wav("res://audio/music/silent.wav") == null: _fail("silent not parsed"); return
\tif g._load_wav("res://audio/music/missing.wav") != null: _fail("missing must be null"); return
\tg.free()
\tprint("SELFTEST OK")
\tquit(0)
"""


# Quest state is DERIVED from flags/items at read time (no runtime quest store): the journal key
# walks the longest satisfied step prefix (resolution flag wins, the final step's own entry is
# preferred once every advance holds), and a failed gate names its unmet leaves in plain words.
_OBJECTIVES_SELFTEST = """
extends SceneTree

func _fail(msg):
\tprint("SELFTEST FAIL: ", msg)
\tquit(1)

func _initialize():
\tvar g = load("res://Game.gd").new()
\tg.ir = {"objectives": [{"id": "o1", "title": "T", "main": true,
\t\t"steps": [
\t\t\t{"id": "s1", "summary": "hear", "advance": {"flag": "heard"}},
\t\t\t{"id": "s2", "summary": "crank", "advance": {"item": "crank"}},
\t\t\t{"id": "s3", "summary": "throw", "resolutions": [{"id": "opened", "flag": "open"}]}],
\t\t"journal": {"offered": "OFF", "s1": "A", "s2": "B", "s3": "FINAL",
\t\t\t"resolved.opened": "R"}}],
\t\t"items": [{"id": "crank", "name": "Crank"}]}
\tg.state = {"flags": {}, "vars": {}, "inv": []}
\tfor it in g.ir["items"]:
\t\tg.item_by_id[it["id"]] = it
\tvar o = g.ir["objectives"][0]
\tif g.journal_text(o) != "OFF": _fail("offered state"); return
\tg.state["flags"]["heard"] = true
\tif g.journal_text(o) != "A": _fail("after s1"); return
\tg.state["inv"].append("crank")
\tif g.journal_text(o) != "FINAL": _fail("final step entry preferred"); return
\tg.state["flags"]["open"] = true
\tif g.journal_text(o) != "R": _fail("resolved"); return
\tif not g.objective_stage(o)["resolved"]: _fail("resolved stage"); return
\tg.state = {"flags": {}, "vars": {}, "inv": []}
\tvar t = g.gate_text("Not yet.", {"all": [{"item": "crank"}, {"flag": "kel_defeated"}]})
\tif t != "Not yet. (needs: Crank, kel defeated)": _fail("gate text: " + t); return
\tif g.gate_text("Not yet.", null) != "Not yet.": _fail("null gate must stay bare"); return
\tg.free()
\tprint("SELFTEST OK")
\tquit(0)
"""


def _run_selftest(tmp_path, script, extra=None):
    proj = tmp_path / "proj"
    shutil.copytree(_RUNTIME, proj)
    for rel, data in (extra or {}).items():
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_bytes(data)
    (proj / "SelfTest.gd").write_text(script)
    proc = subprocess.run(
        [_GODOT, "--headless", "--path", str(proj), "--script", "res://SelfTest.gd"],
        capture_output=True, text=True, timeout=120)
    assert "SELFTEST OK" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_ircore_parity(tmp_path):
    _run_selftest(tmp_path, _SELFTEST)


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_combat_resolution_and_menu_flow(tmp_path):
    _run_selftest(tmp_path, _COMBAT_SELFTEST)


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_overworld_snaps_spawn_to_open(tmp_path):
    _run_selftest(tmp_path, _OVERWORLD_SELFTEST)


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_objective_stage_and_gate_text(tmp_path):
    _run_selftest(tmp_path, _OBJECTIVES_SELFTEST)


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_runtime_parses_music_wavs(tmp_path):
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from utils.audio import ambient_pad_bytes, write_silent_wav
    silent = tmp_path / "s.wav"
    write_silent_wav(silent, seconds=1.0)
    _run_selftest(tmp_path, _MUSIC_SELFTEST, extra={
        "audio/music/music_main.wav": ambient_pad_bytes("music_main"),
        "audio/music/silent.wav": silent.read_bytes(),
    })


def test_game_plays_music_per_place_and_scene():
    src = (_RUNTIME / "Game.gd").read_text()
    assert "func play_music(" in src and "func _load_wav(" in src
    assert "AudioStreamWAV.LOOP_FORWARD" in src
    # place entry + VN boot both trigger a bed; re-entry is a no-op (track compared before play)
    world = src.split("func _run_world(")[1].split("\nfunc ")[0]
    assert "play_music(_track_for_place(place_id))" in world
    assert "if track_id == null or track_id == _music_now:" in src
    vn = (_RUNTIME / "vn.gd").read_text()
    assert "g.play_music(g._track_for_location(node[\"location\"]))" in vn


def test_overworld_spawn_never_lands_on_wall():
    # New Game enters the start place with no arrival spawn -> the avatar defaults to (0,0), a
    # bordered map's wall corner. Both presenters must route the initial cell through
    # _nearest_open (defined once on the 2D presenter, reused by the 3D one) so the player is
    # never boxed in and unable to move.
    ov = (_RUNTIME / "overworld.gd").read_text()
    ov3d = (_RUNTIME / "overworld3d.gd").read_text()
    assert "func _nearest_open" in ov
    assert "_nearest_open(sx, sy, blocked, gw, gh)" in ov
    assert "_helper._nearest_open(sx, sy, blocked, gw, gh)" in ov3d


def test_overworld3d_renders_covered_footprint_cells_as_ground():
    # A footprint's cells are blocked by construction, so the tile pass raised a wall box around
    # every set piece — a spot-size mesh drowned in it entirely (played as "missing"), a tall one
    # stood in a grey cube on a wall-textured plinth. Cells whose feature actually renders become
    # OPEN GROUND (dominant open theme, no box, no blocked tint); a footprint with no art keeps
    # the blocked mosaic (an invisible obstacle is worse).
    src = (_RUNTIME / "overworld3d.gd").read_text()
    assert "func _covered_cells" in src
    assert "_covered_cells(place.get(\"footprints\", {}))" in src
    assert 'if role == "blocked" and covered.has(_helper._key(cx, cy)):' in src
    assert "func _dominant_open_theme" in src
    body = src.split("func _covered_cells(")[1].split("\nfunc ")[0]
    assert "feature_%s.glb" in body and "feature_%s.png" in body


def test_overworld3d_prefers_prop_mesh_over_billboard():
    # An examine/use hotspot is a physical object; when its prop mesh exists it stands in the
    # world as geometry — the flat billboard icon is only the no-mesh fallback.
    src = (_RUNTIME / "overworld3d.gd").read_text()
    assert 'prop_%s.glb' in src
    idx_mesh = src.index("prop_%s.glb")
    idx_icon = src.index("_interactable_icon(it)")
    assert idx_mesh < idx_icon   # mesh probe gates the billboard path


def test_interactable_icon_covers_combat_and_bare_use():
    # start_combat had NO icon branch and a use hotspot with no item clause probed nothing —
    # both rendered as bare colour chips in the world.
    src = (Path(__file__).parent.parent / "src/godot/runtime/overworld.gd").read_text()
    body = src.split("func _interactable_icon(")[1].split("\nfunc ")[0]
    assert "marker_combat.png" in body
    assert body.count('prop_%s.png') == 2   # examine label prop AND the use-label fallback


def test_combat_checks_end_after_status_tick():
    body = (_RUNTIME / "combat.gd").read_text().split("func _run_enc(")[1].split("\nfunc ")[0]
    assert body.index("_any_alive") < body.index("_tick_statuses(u)")
    assert "_check_end" in body[body.index("_tick_statuses(u)"):body.index("await _player_turn")]


def test_flow_routes_menu_end():
    flow = (_RUNTIME / "Game.gd").read_text().split("func _flow(")[1].split("\nfunc ")[0]
    assert '"menu"' in flow
    assert "menu_pick" in flow


def test_overworld_has_wall_face_treatment():
    # a flat full-tile texture reads as ground — blocked cells over open ground must draw a
    # face band + drop shadow (2.5D), and tokens/marker assets must be probed
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "src/godot/runtime/overworld.gd").read_text()
    assert "below_open" in src and "face.color" in src and "shadow" in src
    assert '_token.png" % cid' in src
    assert 'marker_signpost.png' in src and 'prop_%s.png' in src


def test_game_title_screen_and_save_contract():
    src = (_RUNTIME / "Game.gd").read_text()
    # title screen before the intro: art backdrop when present, Continue gated on the save file
    assert "title_card.png" in src
    assert "FileAccess.file_exists(SAVE_PATH)" in src
    assert 'const SAVE_PATH := "user://save.json"' in src
    # the save carries the full resume context: state + place + overworld cell
    assert '"state": state' in src and '"place": _place_id' in src
    assert '{"cell": avatar_cell}' in src
    boot = src.split("func _boot(")[1].split("\nfunc ")[0]
    assert "_load_save" in boot
    assert '_run_world(data["place"], data.get("spawn"))' in boot


def test_game_pause_menu_and_ending_screen():
    src = (_RUNTIME / "Game.gd").read_text()
    assert '"ui_pause": [KEY_ESCAPE]' in src
    pause = src.split("func pause_menu(")[1].split("\nfunc ")[0]
    assert "save_game()" in pause and "get_tree().quit()" in pause
    ending = src.split("func show_ending(")[1].split("\nfunc ")[0]
    assert 'ir.get("endings", [])' in ending
    assert "reload_current_scene" in ending and "quit()" in ending


def test_inventory_strip_dirty_check_refresh():
    src = (_RUNTIME / "Game.gd").read_text()
    proc = src.split("func _process(")[1].split("\nfunc ")[0]
    assert "_inv_last" in proc  # value compare, not a rebuild every frame
    assert '"item_%s.png" % iid' in proc and '"%s.png" % iid' in proc
    assert 'get("name"' in proc  # name-chip fallback when there is no icon art
    assert "_title_open" in proc  # hidden while the title screen is up


def test_presenters_poll_pause():
    for name in ("pnc.gd", "overworld.gd"):
        src = (_RUNTIME / name).read_text()
        assert 'Input.is_action_just_pressed("ui_pause")' in src, name
        assert "pause_menu()" in src, name


def test_pnc_hotspot_hover():
    src = (_RUNTIME / "pnc.gd").read_text()
    assert "mouse_entered" in src and "mouse_exited" in src
    assert "b.modulate" in src and "hover.visible = true" in src


def test_overworld_avatar_cell_and_token_word_match():
    src = (_RUNTIME / "overworld.gd").read_text()
    assert 'g.avatar_cell = {"x": ax, "y": ay}' in src
    assert '(" " + label + " ").contains(" " + nm + " ")' in src
