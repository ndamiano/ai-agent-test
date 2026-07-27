"""The interface contract: the artifact, the derived manifest, the review's patch ops and slicing,
and the review/amend step machines driven turn by turn."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.codegen import build_steps, interfaces, scaffold
from maestro.codegen.scaffold import seed_scaffold
from maestro.codegen.build_state import FixCursor, error_to_dict
from maestro.modules.module import Error, ErrorType

_SPEC = {"frozen": True, "mode": "2d", "design": {"title": "Test"}}

_IFACE = {
    "state": [
        {"field": "deck", "type": "CardId[]", "meaning": "cards left",
         "lifetime": "run", "owner": "initGame", "mutators": ["drawCard"], "readers": []},
        {"field": "hand", "type": "Card[]", "meaning": "cards in hand",
         "lifetime": "level", "owner": "startFight", "mutators": ["drawCard"], "readers": []},
        {"field": "hp", "type": "number", "meaning": "health",
         "lifetime": "run", "owner": "initGame", "mutators": ["takeDamage"], "readers": []},
    ],
    "functions": [
        {"name": "initGame", "file": "game.ts", "signature": "initGame(): void", "purpose": "boot",
         "reads": [], "writes": ["deck", "hp"], "calls": [], "invariants": []},
        {"name": "startFight", "file": "combat.ts", "signature": "startFight(): void",
         "purpose": "begin a fight", "reads": [], "writes": ["hand"], "calls": [], "invariants": []},
        {"name": "drawCard", "file": "combat.ts", "signature": "drawCard(): void",
         "purpose": "draw", "reads": ["deck"], "writes": ["hand"], "calls": [], "invariants": []},
    ],
    "invariants": ["hp never exceeds maxHp"],
}


def _iface():
    return json.loads(json.dumps(_IFACE))


# ── artifact ──────────────────────────────────────────────────────────────────
def test_normalize_drops_unnamed_entries_and_coerces_lists(tmp_path):
    out = interfaces.normalize({
        "state": [{"field": "a", "type": "x[]"}, {"type": "orphan"}],
        "functions": [{"name": "f", "reads": "not-a-list"}, {"purpose": "unnamed"}],
        "invariants": "not-a-list",
    })
    assert [f["field"] for f in out["state"]] == ["a"]
    assert [f["name"] for f in out["functions"]] == ["f"]
    assert out["functions"][0]["reads"] == []
    assert out["invariants"] == []


def test_save_load_roundtrip(tmp_path):
    interfaces.save(tmp_path, _iface())
    assert interfaces.load(tmp_path)["state"][0]["field"] == "deck"


def test_load_missing_or_corrupt_is_none(tmp_path):
    assert interfaces.load(tmp_path) is None
    interfaces.path_of(tmp_path).write_text("{not json", encoding="utf-8")
    assert interfaces.load(tmp_path) is None


# ── derived manifest ──────────────────────────────────────────────────────────
def _game_dir(tmp_path):
    (tmp_path / "game").mkdir()
    return tmp_path


def test_manifest_derives_files_from_function_assignments(tmp_path):
    m = interfaces.manifest_from(_iface(), _game_dir(tmp_path), ["createState", "update"])
    by_name = {f["name"]: f for f in m["files"]}
    assert set(by_name) == {"game.ts", "combat.ts"}
    assert by_name["combat.ts"]["exports"] == ["startFight", "drawCard"]
    # the entry hook carries its own declared functions PLUS the scaffold's hooks
    assert by_name["game.ts"]["exports"] == ["initGame", "createState", "update"]


def test_manifest_appends_entry_hook_when_architecture_omits_it(tmp_path):
    iface = _iface()
    for f in iface["functions"]:
        f["file"] = "combat.ts"
    m = interfaces.manifest_from(iface, _game_dir(tmp_path), ["createState"])
    entry = next(f for f in m["files"] if f["name"] == "game.ts")
    assert entry["exports"] == ["createState"]


def test_manifest_drops_generated_files(tmp_path):
    """A GENERATED file is refused by the write tool, so planning one would strand authoring."""
    run_dir = _game_dir(tmp_path)
    (run_dir / "game" / "world.ts").write_text("// GENERATED\nexport const WORLD = {};", encoding="utf-8")
    iface = _iface()
    iface["functions"].append({"name": "buildTown", "file": "world.ts", "signature": "buildTown()",
                               "purpose": "x", "reads": [], "writes": [], "calls": [],
                               "invariants": []})
    m = interfaces.manifest_from(iface, run_dir, ["createState"])
    assert "world.ts" not in {f["name"] for f in m["files"]}


# ── generated state type ──────────────────────────────────────────────────────
def _state_ts(tmp_path):
    return (tmp_path / "game" / "state.ts").read_text()


def test_state_ts_declares_every_field_with_its_type(tmp_path):
    interfaces.generate_state_ts(_game_dir(tmp_path), _iface())
    src = _state_ts(tmp_path)
    assert "export interface GameState {" in src
    assert "deck: CardId[];" in src
    assert "hand: Card[];" in src
    assert "hp: number;" in src
    assert "lifetime=run, owner=initGame" in src      # the contract rides as a doc comment


def test_state_ts_is_generated_so_the_tools_refuse_to_edit_it(tmp_path):
    """The architecture is what changes the shape — an edit here would silently diverge from it."""
    interfaces.generate_state_ts(_game_dir(tmp_path), _iface())
    assert _state_ts(tmp_path).startswith("// GENERATED")


def test_state_ts_nests_dotted_fields(tmp_path):
    iface = {"state": [{"field": "fight.hand", "type": "Card[]", "lifetime": "level"},
                       {"field": "fight.turn", "type": "number", "lifetime": "turn"},
                       {"field": "gold", "type": "number", "lifetime": "run"}]}
    interfaces.generate_state_ts(_game_dir(tmp_path), iface)
    src = _state_ts(tmp_path)
    assert "fight: {" in src and "hand: Card[];" in src and "turn: number;" in src
    assert "gold: number;" in src


def test_state_ts_with_no_declared_state_still_typechecks(tmp_path):
    interfaces.generate_state_ts(_game_dir(tmp_path), {"state": []})
    assert "[key: string]: any;" in _state_ts(tmp_path)


def test_manifest_never_asks_anyone_to_author_the_generated_state(tmp_path):
    run_dir = _game_dir(tmp_path)
    iface = _iface()
    interfaces.generate_state_ts(run_dir, iface)
    iface["functions"].append({"name": "helper", "file": "state.ts", "purpose": "x"})
    m = interfaces.manifest_from(iface, run_dir, ["createState"])
    assert "state.ts" not in {f["name"] for f in m["files"]}


def test_interfaces_step_emits_the_state_type(tmp_path):
    _game_dir(tmp_path)
    fc = _fix("interfaces", "interfaced")
    build_steps.interfaces_step(_SPEC, tmp_path, {}, fc, {})
    build_steps.interfaces_step(_SPEC, tmp_path, {}, fc, _reply(_iface()))
    assert "deck: CardId[];" in _state_ts(tmp_path)


def test_review_regenerates_the_state_type_after_patching_it(tmp_path):
    _game_dir(tmp_path)
    interfaces.save(tmp_path, _iface())
    interfaces.generate_state_ts(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    build_steps.review_step(_SPEC, tmp_path, {}, fc, {})
    build_steps.review_step(_SPEC, tmp_path, {}, fc,
                            _reply({"problems_found": [{"problem": "deck should hold whole cards"}]}))
    build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": [
        {"op": "replace_state", "field": "deck", "value": {**_IFACE["state"][0], "type": "Card[]"}}]}))
    for _ in range(6):
        out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"problems_found": []}))
        if isinstance(out, build_steps.Done):
            break
    assert "deck: Card[];" in _state_ts(tmp_path)


# ── review: patch ops ─────────────────────────────────────────────────────────
def test_replace_state_requires_the_complete_object():
    iface = _iface()
    errs = interfaces.apply_patches(iface, [{"op": "replace_state", "field": "deck", "value": {}}])
    assert errs and "COMPLETE" in errs[0]
    assert iface["state"][0]["owner"] == "initGame"


def test_patch_ops_add_replace_delete():
    iface = _iface()
    errs = interfaces.apply_patches(iface, [
        {"op": "replace_state", "field": "deck",
         "value": {**_IFACE["state"][0], "owner": "startFight"}},
        {"op": "delete_function", "name": "drawCard"},
        {"op": "add_state", "value": {"field": "gold", "type": "number", "meaning": "money",
                                      "lifetime": "run", "owner": "initGame"}},
        {"op": "set_invariants", "value": ["one"]},
    ])
    assert errs == []
    assert iface["state"][0]["owner"] == "startFight"
    assert [f["name"] for f in iface["functions"]] == ["initGame", "startFight"]
    assert iface["state"][-1]["field"] == "gold"
    assert iface["invariants"] == ["one"]


def test_patch_rejects_unknown_target_and_duplicate_add():
    iface = _iface()
    errs = interfaces.apply_patches(iface, [
        {"op": "replace_function", "name": "nope", "value": {"name": "nope"}},
        {"op": "add_state", "value": {"field": "deck", "type": "x[]"}},
        {"op": "frobnicate", "value": {}},
    ])
    assert len(errs) == 3
    assert "no name named 'nope'" in errs[0]
    assert "already exists" in errs[1]
    assert "unknown op" in errs[2]


def test_dry_run_mutates_nothing():
    iface = _iface()
    before = json.dumps(iface)
    errs = interfaces.apply_patches(iface, [{"op": "delete_state", "field": "deck"}], dry=True)
    assert errs == []
    assert json.dumps(iface) == before


# ── review: slicing ───────────────────────────────────────────────────────────
def test_small_architecture_is_one_slice():
    assert interfaces.slices(_iface()) == [_iface()]


def test_large_architecture_splits_functions_but_keeps_state_whole():
    iface = _iface()
    iface["functions"] = [{"name": f"fn{i}", "file": "game.ts"} for i in range(20)]
    parts = interfaces.slices(iface, chunk=8)
    assert [len(p["functions"]) for p in parts] == [8, 8, 4]
    assert all(p["state"] == iface["state"] for p in parts)


# ── the kit's law over the declared architecture ──────────────────────────────
def _hook_iface(signature, name="draw", file="game.ts"):
    return {"state": [], "invariants": [],
            "functions": [{"name": name, "file": file, "signature": signature, "purpose": "p",
                           "reads": [], "writes": [], "calls": [], "invariants": []}]}


def test_hook_signatures_are_read_from_the_kit_types(tmp_path):
    hooks = interfaces.hook_signatures(tmp_path)
    assert hooks["createState"] == "createState(kit: Kit): GameState"
    assert hooks["update"] == "update(state: GameState, dt: number, input: Input, kit: Kit): void"


def test_a_declared_hook_signature_is_overwritten_by_the_kit_s(tmp_path):
    """main.ts is GENERATED and calls the hooks with fixed arity, so a hook signature the
    architecture invents is authored faithfully into every file and only surfaces at tsc."""
    iface = _hook_iface("hud(state: GameState, ctx: CanvasRenderingContext2D): HudItem[]", name="hud")
    fixed = interfaces.enforce_kit_contract(tmp_path, iface)
    assert iface["functions"][0]["signature"] == interfaces.hook_signatures(tmp_path)["hud"]
    assert fixed and "hud" in fixed[0]


def test_a_hook_name_in_another_file_is_left_alone(tmp_path):
    """Only game.ts carries the scaffold's hooks; a local `update` elsewhere is the game's own."""
    sig = "update(world: World): void"
    iface = _hook_iface(sig, name="update", file="level.ts")
    assert not [c for c in interfaces.enforce_kit_contract(tmp_path, iface) if "update" in c]
    assert iface["functions"][0]["signature"] == sig


def test_dom_draw_types_are_rewritten_to_the_kit_surface(tmp_path):
    iface = _hook_iface("drawTilemap(ctx: CanvasRenderingContext2D, t: string[][]): void",
                        name="drawTilemap", file="render.ts")
    iface["state"] = [{"field": "ctx", "type": "CanvasRenderingContext2D", "lifetime": "run",
                       "owner": "init", "mutators": [], "readers": []}]
    interfaces.enforce_kit_contract(tmp_path, iface)
    assert iface["functions"][0]["signature"] == "drawTilemap(ctx: DrawApi, t: string[][]): void"
    assert iface["state"][0]["type"] == "DrawApi"


def test_save_enforces_the_kit_contract(tmp_path):
    """Every write of the architecture goes through save, so no path can bypass the kit's law."""
    interfaces.save(tmp_path, _hook_iface("draw(ctx: CanvasRenderingContext2D): void"))
    assert "CanvasRenderingContext2D" not in json.dumps(interfaces.load(tmp_path))


# ── the scaffold's state, appended rather than asked for ─────────────────────
_SCHEMES = ["top-down", "platformer", "grid-turn", "orbital-3d", "vehicle-3d", "first-person-3d",
            "follow-3d", "nonsense"]


def _seed(tmp_path, scheme="top-down", **spec):
    spec = {"mode": "3d" if scheme.endswith("-3d") else "2d",
            "design": {"control": {"scheme": scheme}, **(spec.pop("design", {}))}, **spec}
    seed_scaffold(SimpleNamespace(run_dir=tmp_path), spec)
    return tmp_path


def test_the_state_the_scaffold_asserts_is_appended_when_the_architecture_omits_it(tmp_path):
    """The design turn is TOLD to declare state.player and a measured build ignored it: state.ts is
    GENERATED from the architecture, so game.ts widened GameState locally to compile, and a sibling
    authored later against the real GameState died on `Property 'player' does not exist`."""
    _seed(tmp_path)
    iface = _iface()
    fixed = interfaces.enforce_kit_contract(tmp_path, iface)
    player = next(f for f in iface["state"] if f["field"] == "player")
    assert player["type"] == "Entity" and player["owner"] == "init"
    assert player["lifetime"] in interfaces.LIFETIMES
    assert any("player" in c for c in fixed)


def test_a_player_the_model_declared_itself_is_left_alone(tmp_path):
    _seed(tmp_path)
    iface = _iface()
    mine = {"field": "player", "type": "Ship", "meaning": "the ship", "lifetime": "level",
            "owner": "startFight", "mutators": [], "readers": []}
    iface["state"].append(mine)
    assert not [c for c in interfaces.enforce_kit_contract(tmp_path, iface) if "player" in c]
    assert [f for f in iface["state"] if f["field"] == "player"] == [mine]


def test_the_appended_field_survives_into_the_generated_state_type(tmp_path):
    _game_dir(tmp_path)
    _seed(tmp_path)
    iface = _iface()
    interfaces.enforce_kit_contract(tmp_path, iface)
    interfaces.generate_state_ts(tmp_path, iface)
    assert "player: Entity;" in (tmp_path / "game" / interfaces.STATE_FILE).read_text()


@pytest.mark.parametrize("scheme", _SCHEMES)
def test_only_the_fields_the_scaffold_cannot_run_without_are_forced(tmp_path, scheme):
    """`state.world ?? []`, `state.tilemap?.solidAt` and `if (state.ground)` are optional BY
    CONSTRUCTION — forcing them into every game's contract would be the pipeline inventing a rule
    rather than stating the kit's."""
    _seed(tmp_path, scheme, world=True, design={"uses": ["dialogue"]})
    assert scaffold.unguarded_state_fields(tmp_path) == ["player"]


def test_every_scaffolded_field_has_a_declaration_to_append(tmp_path):
    """A template that starts asserting a new field must bring its type/owner with it, or the
    enforcement silently skips it and the gap reopens."""
    for scheme in _SCHEMES:
        for extra in ({}, {"world": True, "design": {"uses": ["dialogue"]}}):
            d = tmp_path / f"{scheme}{len(extra)}"
            (d / "game").mkdir(parents=True)
            _seed(d, scheme, **extra)
            assert set(scaffold.unguarded_state_fields(d)) <= set(interfaces._SCAFFOLD_STATE)


def test_save_appends_the_scaffolds_state(tmp_path):
    """Every write of the architecture goes through save, so no path can bypass this either."""
    _seed(tmp_path)
    interfaces.save(tmp_path, {"state": [], "functions": [], "invariants": []})
    assert [f["field"] for f in interfaces.load(tmp_path)["state"]] == ["player", "world"]


def test_nothing_is_appended_before_the_scaffold_is_seeded(tmp_path):
    iface = _iface()
    interfaces.enforce_kit_contract(tmp_path, iface)
    assert not [f for f in iface["state"] if f["field"] == "player"]


# ── the kit's own state, which the architecture does not get to omit ──────────
def _spec_on_disk(tmp_path, uses):
    (tmp_path / "spec.json").write_text(json.dumps(
        {"mode": "3d", "design": {"control": {"scheme": "orbital-3d"}, "uses": uses}}))


def test_the_state_the_engine_renders_from_is_always_declared(tmp_path):
    """An entity is visible because it is in state.world. A GameState without it is a contract the
    game cannot be written against: a measured build burned 90 steps on `Property 'world' does not
    exist on type 'GameState'`, which no edit to game.ts can settle."""
    _seed(tmp_path)
    iface = _iface()
    fixed = interfaces.enforce_kit_contract(tmp_path, iface)
    world = next(f for f in iface["state"] if f["field"] == "world")
    assert world["type"] == "World" and world["owner"] == "init"
    assert any("world" in c for c in fixed)


def test_the_dialogue_state_the_kit_writes_is_declared_when_the_spec_talks(tmp_path):
    """kit.talkOpen ASSIGNS state.talk — the game only reads it, so nothing in the code declares it."""
    _seed(tmp_path, "orbital-3d", world=True, design={"uses": ["dialogue"]})
    _spec_on_disk(tmp_path, ["dialogue"])
    iface = _iface()
    interfaces.enforce_kit_contract(tmp_path, iface)
    assert [f["field"] for f in iface["state"] if f["field"] in ("talk", "quests")] == ["talk"]


def test_a_kit_field_whose_primitive_the_spec_never_composes_is_not_declared(tmp_path):
    """An unused row in the state table is one more thing for a small model to build to."""
    _seed(tmp_path)
    _spec_on_disk(tmp_path, ["particles"])
    iface = _iface()
    interfaces.enforce_kit_contract(tmp_path, iface)
    assert not [f for f in iface["state"] if f["field"] in ("talk", "quests")]


def test_a_kit_field_the_model_declared_itself_is_left_alone(tmp_path):
    _seed(tmp_path)
    iface = _iface()
    mine = {"field": "world", "type": "Entity[]", "meaning": "everything alive", "lifetime": "level",
            "owner": "startFight", "mutators": [], "readers": []}
    iface["state"].append(mine)
    interfaces.enforce_kit_contract(tmp_path, iface)
    assert [f for f in iface["state"] if f["field"] == "world"] == [mine]


def test_the_kit_state_the_kit_creates_on_demand_is_optional_in_the_generated_type(tmp_path):
    """`world` exists from frame 0; `talk` does not exist until the player talks, so a required
    `talk` would force every createState to invent one."""
    _game_dir(tmp_path)
    _seed(tmp_path, "orbital-3d", world=True, design={"uses": ["dialogue"]})
    _spec_on_disk(tmp_path, ["dialogue", "quest"])
    iface = _iface()
    interfaces.enforce_kit_contract(tmp_path, iface)
    interfaces.generate_state_ts(tmp_path, iface)
    src = _state_ts(tmp_path)
    assert "world: World;" in src
    assert "talk?: Talk;" in src and "quests?: Quest[];" in src


def test_hooks_block_gives_the_architecture_turn_the_signatures_and_the_vocabulary(tmp_path):
    block = interfaces.hooks_block(tmp_path, _SPEC)
    assert "createState(kit: Kit): GameState" in block
    assert "DrawApi" in block and "no DOM" in block


def test_state_ts_block_names_the_ambient_types_it_must_not_import(tmp_path):
    """Authoring is told to import types from the file that owns them; kit types are owned by no
    file, so without this the instruction generalizes into importing them from ./state.ts."""
    _game_dir(tmp_path)
    interfaces.generate_state_ts(tmp_path, _iface())
    block = interfaces.state_ts_block(tmp_path)
    assert "AMBIENT" in block and "Kit" in block and "Input" in block
    assert "exports GameState AND NOTHING ELSE" in block


def test_hooks_block_names_the_state_the_scaffold_steers(tmp_path):
    """The architecture turn runs before main.ts exists, so without this it omits state.player and
    the game crashes at frame 0 on an assert no fix loop can satisfy — state.ts is GENERATED."""
    block = interfaces.hooks_block(tmp_path, {"mode": "2d",
                                              "design": {"control": {"scheme": "top-down"}}})
    assert "player" in block and "world" in block
    assert "never import them" in block


def test_hooks_block_omits_draw_for_a_3d_game(tmp_path):
    """The renderer draws 3D from entity shape tags — listing draw would contradict the rule the
    same prompt states."""
    block = interfaces.hooks_block(tmp_path, {"mode": "3d"})
    assert "draw(" not in block
    assert "hud(state: GameState, kit: Kit): HudItem[]" in block


def test_hooks_block_says_a_3d_position_has_three_coordinates(tmp_path):
    """A measured 3D build declared its plot/animal positions Vec2, authored the whole game against
    it, and every site needing a z was a tsc error the fix loop could not settle."""
    assert "Vec3" in interfaces.hooks_block(tmp_path, {"mode": "3d"})
    assert "Vec3 {x, y, z}" not in interfaces.hooks_block(tmp_path, {"mode": "2d"})


# ── review step machine ───────────────────────────────────────────────────────
def _reply(obj):
    return {"choices": [{"message": {"content": "```json\n" + json.dumps(obj) + "\n```"}}]}


def _fix(shape, code, path=None):
    err = Error(type=ErrorType.BUILD, code=code, component="game", path=path, message="x")
    return FixCursor(shape=shape, error=error_to_dict(err))


def test_review_converges_when_a_round_finds_nothing(tmp_path):
    interfaces.save(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    assert isinstance(build_steps.review_step(_SPEC, tmp_path, {}, fc, {}), build_steps.Infer)
    out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"problems_found": []}))
    assert isinstance(out, build_steps.Done)
    assert interfaces.load(tmp_path)["reviewed"] is True


def test_review_finds_then_patches_then_re_finds(tmp_path):
    interfaces.save(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    build_steps.review_step(_SPEC, tmp_path, {}, fc, {})
    problem = {"problem": "deck is owned by initGame but startFight replaces it"}
    out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"problems_found": [problem]}))
    assert isinstance(out, build_steps.Infer) and fc.mode == "patch"

    patch = {"op": "replace_state", "field": "deck",
             "value": {**_IFACE["state"][0], "mutators": ["drawCard", "startFight"]}}
    out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": [patch]}))
    assert isinstance(out, build_steps.Infer) and fc.mode == "find" and fc.rnd == 2
    assert interfaces.load(tmp_path)["state"][0]["mutators"] == ["drawCard", "startFight"]


def test_review_logs_what_each_round_found_and_changed(tmp_path):
    """The review patches in place, so without a durable log 'which contradiction did it fix?' is
    unanswerable once the run is over."""
    interfaces.save(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    build_steps.review_step(_SPEC, tmp_path, {}, fc, {})
    problem = {"problem": "deck is owned by initGame but startFight replaces it",
               "why_it_breaks": "progress is wiped each fight"}
    build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"problems_found": [problem]}))
    good = {"op": "replace_state", "field": "deck",
            "value": {**_IFACE["state"][0], "mutators": ["drawCard", "startFight"]}}
    bad = {"op": "delete_state", "field": "ghost"}
    # the bad op is rejected on the dry run, so the first turn spends the one retry
    build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": [good, bad]}))
    build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": [good, bad]}))

    entries = [json.loads(l) for l in
               (tmp_path / interfaces.REVIEW_LOG).read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["round"] == 1
    assert entries[0]["problems"][0]["why_it_breaks"] == "progress is wiped each fight"
    assert entries[0]["patches"] == [good, bad]
    assert entries[0]["rejected"] and "ghost" in entries[0]["rejected"][0]


def test_review_reports_a_repeated_problem_only_once(tmp_path):
    interfaces.save(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    build_steps.review_step(_SPEC, tmp_path, {}, fc, {})
    problem = {"problem": "deck ownership is ambiguous"}
    build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"problems_found": [problem]}))
    build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": []}))
    # round 2 re-reads the same architecture and re-reports it; nothing fresh, so review is done
    out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"problems_found": [problem]}))
    assert isinstance(out, build_steps.Done)


def test_review_retries_once_on_unapplicable_ops_then_moves_on(tmp_path):
    interfaces.save(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    build_steps.review_step(_SPEC, tmp_path, {}, fc, {})
    build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"problems_found": [{"problem": "p"}]}))
    bad = {"op": "replace_state", "field": "nope", "value": {"field": "nope"}}
    out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": [bad]}))
    assert isinstance(out, build_steps.Infer) and fc.patch_retry == 1
    out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": [bad]}))
    assert isinstance(out, build_steps.Infer) and fc.rnd == 2   # gave up on it, next round


def test_review_stops_at_the_round_cap(tmp_path):
    interfaces.save(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    build_steps.review_step(_SPEC, tmp_path, {}, fc, {})
    for i in range(build_steps._REVIEW_ROUNDS):
        build_steps.review_step(_SPEC, tmp_path, {}, fc,
                                _reply({"problems_found": [{"problem": f"p{i}"}]}))
        out = build_steps.review_step(_SPEC, tmp_path, {}, fc, _reply({"patches": []}))
    assert isinstance(out, build_steps.Done)
    assert interfaces.load(tmp_path)["reviewed"] is True


def test_unparseable_review_turn_does_not_strand_the_build(tmp_path):
    interfaces.save(tmp_path, _iface())
    fc = _fix("review", "reviewed")
    build_steps.review_step(_SPEC, tmp_path, {}, fc, {})
    out = build_steps.review_step(_SPEC, tmp_path, {}, fc,
                                  {"choices": [{"message": {"content": "sorry, thinking..."}}]})
    assert isinstance(out, build_steps.Done)


# ── interfaces step ───────────────────────────────────────────────────────────
def test_interfaces_step_writes_the_architecture_and_the_manifest(tmp_path):
    _game_dir(tmp_path)
    fc = _fix("interfaces", "interfaced")
    assert isinstance(build_steps.interfaces_step(_SPEC, tmp_path, {}, fc, {}), build_steps.Infer)
    out = build_steps.interfaces_step(_SPEC, tmp_path, {}, fc, _reply(_iface()))
    assert isinstance(out, build_steps.Done)
    assert interfaces.load(tmp_path)["state"][0]["field"] == "deck"
    manifest = json.loads((tmp_path / "game" / "manifest.json").read_text())
    assert {f["name"] for f in manifest["files"]} == {"game.ts", "combat.ts"}


def test_an_empty_architecture_is_never_written(tmp_path):
    """A lost completion re-drives the turn with an empty result. Writing that would declare no
    contracts at all — nothing to author and nothing to check — so it must not satisfy the gate."""
    _game_dir(tmp_path)
    fc = _fix("interfaces", "interfaced")
    build_steps.interfaces_step(_SPEC, tmp_path, {}, fc, {})
    out = build_steps.interfaces_step(_SPEC, tmp_path, {}, fc, {})
    assert isinstance(out, build_steps.Done)     # back to outer, where `interfaced` fires again
    assert interfaces.load(tmp_path) is None
    assert not (tmp_path / "game" / "manifest.json").exists()


def test_an_empty_architecture_does_not_overwrite_a_good_one(tmp_path):
    _game_dir(tmp_path)
    interfaces.save(tmp_path, _iface())
    fc = _fix("interfaces", "interfaced")
    build_steps.interfaces_step(_SPEC, tmp_path, {}, fc, {})
    build_steps.interfaces_step(_SPEC, tmp_path, {}, fc,
                               {"choices": [{"message": {"content": "not json"}}]})
    assert len(interfaces.load(tmp_path)["functions"]) == 3


def test_interfaced_gate_is_unsatisfied_by_an_empty_architecture(tmp_path):
    from maestro.codegen.module import _detect_interfaced

    class _Ctx:
        state = type("S", (), {"run_dir": tmp_path})()

    interfaces.save(tmp_path, {"state": [], "functions": [], "invariants": []})
    assert [e.code for e in _detect_interfaced(None, None, _Ctx())] == ["interfaced"]
    interfaces.save(tmp_path, _iface())
    assert _detect_interfaced(None, None, _Ctx()) == []


# ── amend step ────────────────────────────────────────────────────────────────
def test_amend_verdict_contract_patches_the_architecture(tmp_path):
    _game_dir(tmp_path)
    interfaces.save(tmp_path, _iface())
    fc = _fix("amend", "typechecks", path="combat.ts")
    assert isinstance(build_steps.amend_step(_SPEC, tmp_path, {}, fc, {}), build_steps.Infer)
    out = build_steps.amend_step(_SPEC, tmp_path, {}, fc, _reply({
        "verdict": "contract", "reason": "startFight is the real owner",
        "patches": [{"op": "replace_state", "field": "deck",
                     "value": {**_IFACE["state"][0], "owner": "startFight"}}]}))
    assert isinstance(out, build_steps.Done)
    assert interfaces.load(tmp_path)["state"][0]["owner"] == "startFight"


def test_amend_regenerates_the_state_type_after_patching(tmp_path):
    """A ruling reached from a TYPE error is a state SHAPE change. Without regenerating state.ts the
    model is handed a verdict it cannot act on: tsc keeps checking the stale GameState."""
    _game_dir(tmp_path)
    interfaces.save(tmp_path, _iface())
    interfaces.generate_state_ts(tmp_path, _iface())
    fc = _fix("amend", "typechecks", path="game.ts")
    build_steps.amend_step(_SPEC, tmp_path, {}, fc, {})
    build_steps.amend_step(_SPEC, tmp_path, {}, fc, _reply({
        "verdict": "contract", "reason": "the game needs a player field",
        "patches": [{"op": "add_state", "value": {"field": "player", "type": "Entity",
                                                  "meaning": "the hero", "lifetime": "run",
                                                  "owner": "initGame", "mutators": [],
                                                  "readers": []}}]}))
    assert "player: Entity;" in (tmp_path / "game" / "state.ts").read_text()


def test_amend_verdict_code_falls_through_to_the_read_edit_subloop(tmp_path):
    _game_dir(tmp_path)
    interfaces.save(tmp_path, _iface())
    fc = _fix("amend", "typechecks", path="combat.ts")
    build_steps.amend_step(_SPEC, tmp_path, {}, fc, {})
    out = build_steps.amend_step(_SPEC, tmp_path, {}, fc,
                                 _reply({"verdict": "code", "reason": "the code broke it"}))
    assert isinstance(out, build_steps.Infer)
    assert fc.mode == "fix" and fc.history      # the read→edit transcript has been opened


def test_amend_falls_through_when_the_contract_verdict_lands_no_patches(tmp_path):
    """A 'contract' verdict whose ops are all unapplicable must not end the fix having changed
    nothing — the violation would still be there and the loop would re-enter forever."""
    _game_dir(tmp_path)
    interfaces.save(tmp_path, _iface())
    fc = _fix("amend", "typechecks", path="combat.ts")
    build_steps.amend_step(_SPEC, tmp_path, {}, fc, {})
    out = build_steps.amend_step(_SPEC, tmp_path, {}, fc, _reply({
        "verdict": "contract",
        "patches": [{"op": "replace_state", "field": "ghost", "value": {"field": "ghost"}}]}))
    assert isinstance(out, build_steps.Infer) and fc.mode == "fix"


def test_derived_manifest_omits_the_draw_hook_for_a_3d_game(tmp_path):
    """A 3D game has no draw — the renderer draws the entities from their shape tags."""
    _game_dir(tmp_path)
    spec = {"frozen": True, "mode": "3d", "design": {}}
    fc = _fix("interfaces", "interfaced")
    build_steps.interfaces_step(spec, tmp_path, {}, fc, {})
    build_steps.interfaces_step(spec, tmp_path, {}, fc, _reply(_iface()))
    manifest = json.loads((tmp_path / "game" / "manifest.json").read_text())
    entry = next(f for f in manifest["files"] if f["name"] == "game.ts")
    assert entry["exports"] == ["initGame", "createState", "init", "update", "hud"]


def test_a_subdirectory_file_is_flattened(tmp_path):
    """A game is a flat folder: `write` drops the path, so a manifest asking for systems/beat.ts
    would never be satisfied by the beat.ts that actually lands."""
    out = interfaces.normalize({"state": [], "invariants": [], "functions": [
        {"name": "updateBeat", "file": "systems/beat.ts", "signature": "updateBeat(): void"},
        {"name": "init", "file": "game.ts", "signature": "init(): void"},
    ]})
    assert [f["file"] for f in out["functions"]] == ["beat.ts", "game.ts"]


def test_flattened_files_group_into_one_manifest_entry(tmp_path):
    _game_dir(tmp_path)
    iface = interfaces.normalize({"state": [], "invariants": [], "functions": [
        {"name": "a", "file": "systems/beat.ts", "signature": "a(): void"},
        {"name": "b", "file": "beat.ts", "signature": "b(): void"},
    ]})
    m = interfaces.manifest_from(iface, tmp_path, ["createState"])
    beat = [f for f in m["files"] if f["name"] == "beat.ts"]
    assert len(beat) == 1 and set(beat[0]["exports"]) == {"a", "b"}


def test_a_replace_state_missing_owner_is_rejected():
    """replace swaps the WHOLE entry, so an omitted key deletes a contract. One measured review
    dropped `owner` from every field, so the generated state.ts documented none."""
    iface = {"state": [{"field": "score", "type": "number", "lifetime": "run", "owner": "init",
                        "mutators": ["addScore"], "readers": ["hud"]}], "functions": []}
    errs = interfaces.apply_patches(iface, [
        {"op": "replace_state", "field": "score",
         "value": {"field": "score", "type": "number", "lifetime": "run",
                   "mutators": ["addScore", "resetScore"], "readers": ["hud"]}}])
    assert errs and "owner" in errs[0]
    assert iface["state"][0]["owner"] == "init"      # the original survives untouched


def test_a_complete_replace_state_still_applies():
    iface = {"state": [{"field": "score", "type": "number", "lifetime": "run", "owner": "init",
                        "mutators": [], "readers": []}], "functions": []}
    errs = interfaces.apply_patches(iface, [
        {"op": "replace_state", "field": "score",
         "value": {"field": "score", "type": "number", "lifetime": "session", "owner": "reset",
                   "mutators": ["addScore"], "readers": ["hud"]}}])
    assert errs == []
    assert iface["state"][0]["lifetime"] == "session" and iface["state"][0]["owner"] == "reset"
