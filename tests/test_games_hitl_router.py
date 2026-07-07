import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.routers import games
from api.build_queue import build_queue
from maestro.state import RunState
from maestro import run_control
from auth.store import User

U = User(id="u1", handle="alice", role="user")
OTHER = User(id="u2", handle="bob", role="user")


def _patch(monkeypatch, base):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(base / rid)))


def _frozen_run(base, run_id="g", modules=None, params=None, owner="u1"):
    state = RunState(base / run_id)
    state.write_spec({"title": "G", "frozen": True, "modules": modules or [], "params": params or {}})
    state.write_owner(owner)
    return state


# ── control endpoints ─────────────────────────────────────────────────────────
def test_control_409_when_no_build(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    run_control.remove("g")
    for fn in (games.pause_game, games.resume_game, games.cancel_game):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(fn("g", user=U))
        assert exc.value.status_code == 409


def test_control_403_for_another_users_run(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path, owner="u1")
    ctrl = run_control.get_or_create("g")
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(games.pause_game("g", user=OTHER))
        assert exc.value.status_code == 403
    finally:
        run_control.remove("g")


def test_pause_resume_cancel_flip_control(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    ctrl = run_control.get_or_create("g")
    try:
        asyncio.run(games.pause_game("g", user=U))
        assert ctrl.paused is True
        asyncio.run(games.resume_game("g", user=U))
        assert ctrl.paused is False
        asyncio.run(games.cancel_game("g", user=U))
        assert ctrl.cancelled is True
    finally:
        run_control.remove("g")


# ── todos + waivers ────────────────────────────────────────────────────────────
def test_todo_endpoints(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)

    todo = asyncio.run(games.add_todo_game("g", games.TodoBody(component_id="premise", text="fix it"),
                                           user=U))
    assert todo["component_id"] == "premise" and todo["done"] is False
    assert state.read_human_todos()[0]["text"] == "fix it"

    asyncio.run(games.resolve_todo_game("g", todo["id"], games.ResolveBody(done=True), user=U))
    assert state.read_human_todos()[0]["done"] is True

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.resolve_todo_game("g", "ghost", games.ResolveBody(), user=U))
    assert exc.value.status_code == 404


def test_todo_403_for_another_user(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path, owner="u1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.add_todo_game("g", games.TodoBody(component_id="premise", text="x"),
                                        user=OTHER))
    assert exc.value.status_code == 403


def test_waive_endpoints(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path, modules=["cast"])    # empty premise -> cast emits real errors
    from maestro.modules.human import effective_failures

    idkey = effective_failures(state.read_spec(), state)[0]["idkey"]
    asyncio.run(games.waive_game("g", games.WaiveBody(idkey=idkey), user=U))
    assert state.read_waivers()[0]["idkey"] == idkey

    asyncio.run(games.unwaive_game("g", games.UnwaiveBody(idkey=idkey), user=U))
    assert state.read_waivers() == []

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.unwaive_game("g", games.UnwaiveBody(idkey="nope"), user=U))
    assert exc.value.status_code == 404


def test_detail_surfaces_human_todos_and_status(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path, modules=["cast"])   # empty premise -> cast BUILD errors
    asyncio.run(games.add_todo_game("g", games.TodoBody(component_id="premise", text="x"), user=U))

    detail = asyncio.run(games.get_game("g", user=U))
    assert detail["status"] == "idle"
    assert len(detail["human_todos"]) == 1
    # the open premise (build) checks + the human todo both show in the effective to-do
    assert {t["type"] for t in detail["todo"]} == {"build", "human"}


# ── edit + regenerate + compile gating ──────────────────────────────────────────
def test_edit_component_writes_when_idle(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    res = asyncio.run(games.edit_component_game(
        "g", "notes", games.ComponentBody(content={"text": "hand-written"}), user=U))
    assert res["ok"] is True
    assert state.read_component("notes") == {"text": "hand-written"}


def test_edit_component_403_for_another_user(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path, owner="u1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.edit_component_game(
            "g", "notes", games.ComponentBody(content={"text": "x"}), user=OTHER))
    assert exc.value.status_code == 403


def test_edit_allowed_while_building_unpaused(tmp_path, monkeypatch):
    """Epic C3: edits are UN-GATED — allowed mid-build, not just while paused/idle."""
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    build_queue._current = "g"
    run_control.get_or_create("g")  # status defaults to "running"
    try:
        res = asyncio.run(games.edit_component_game(
            "g", "notes", games.ComponentBody(content={"x": 1}), user=U))
        assert res["ok"] is True
        assert state.read_component("notes") == {"x": 1}
    finally:
        build_queue._current = None
        run_control.remove("g")


def test_edit_allowed_while_paused(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    build_queue._current = "g"
    ctrl = run_control.get_or_create("g")
    ctrl.set_status("paused")
    try:
        res = asyncio.run(games.edit_component_game(
            "g", "notes", games.ComponentBody(content={"x": 1}), user=U))
        assert res["ok"] is True
        assert state.read_component("notes") == {"x": 1}
    finally:
        build_queue._current = None
        run_control.remove("g")


def test_edit_node_patches_line(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("nodes", {"node_ids": ["n1"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "old"}], "end": {"type": "end"}}}})

    res = asyncio.run(games.edit_node_game("g", "n1", games.NodeEditBody(line_index=0, text="new"),
                                           user=U))
    assert res["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"]["lines"][0]["text"] == "new"


def test_compile_dispatches_to_engine(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    calls = {}

    def fake_compile_for(engine):
        def _c(run_dir, distribute=False):
            calls["engine"] = engine
            calls["distribute"] = distribute
            return {"ok": True, "reason": None}
        return _c

    monkeypatch.setattr("maestro.engines.compile_for", fake_compile_for)
    res = asyncio.run(games.compile_game("g", games.CompileBody(distribute=True), user=U))
    assert res["ok"] is True
    assert calls == {"engine": "renpy", "distribute": True}


def test_compile_still_blocked_while_building_unpaused(tmp_path, monkeypatch):
    """Only EDITS were un-gated (C3) — compile/regenerate/rewrite still race the build thread."""
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    build_queue._current = "g"
    run_control.get_or_create("g")
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(games.compile_game("g", games.CompileBody(), user=U))
        assert exc.value.status_code == 409
    finally:
        build_queue._current = None
        run_control.remove("g")


# ── Epic C: asset browser (list / detail / edit / dirty / thumbs) ────────────────────────────
def _events(monkeypatch):
    """Capture every event the router publishes, without needing a running event loop."""
    calls = []
    monkeypatch.setattr("api.websocket.event_bus.event_bus.publish_sync", calls.append)
    return calls


def test_list_assets_uniform_shape_and_folds_in_dirty(tmp_path, monkeypatch):
    _events(monkeypatch)
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("characters", {"characters": [
        {"id": "mara", "name": "Mara"}, {"id": "jonas", "name": "Jonas"}]})
    from maestro.modules.human import set_dirty
    set_dirty(state, "characters:mara", "make her warmer")

    rows = asyncio.run(games.list_assets_game("g", "characters", user=U))

    assert {r["id"] for r in rows} == {"mara", "jonas"}
    mara = next(r for r in rows if r["id"] == "mara")
    assert mara == {"component": "characters", "id": "mara", "idkey": "characters:mara",
                    "content": {"id": "mara", "name": "Mara"}, "dirty": True,
                    "review_note": "make her warmer"}
    jonas = next(r for r in rows if r["id"] == "jonas")
    assert jonas["dirty"] is False and jonas["review_note"] == ""


def test_list_assets_403_for_another_user(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path, owner="u1")
    state.write_component("characters", {"characters": [{"id": "mara", "name": "Mara"}]})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.list_assets_game("g", "characters", user=OTHER))
    assert exc.value.status_code == 403


def test_list_assets_404_for_unknown_component(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.list_assets_game("g", "characters", user=U))
    assert exc.value.status_code == 404


def test_get_asset_detail_and_404(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("items", {"items": [{"id": "item_key", "name": "Key"}]})

    row = asyncio.run(games.get_asset_game("g", "items", "item_key", user=U))
    assert row["content"]["name"] == "Key"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.get_asset_game("g", "items", "item_ghost", user=U))
    assert exc.value.status_code == 404


def test_combat_assets_are_flattened_across_sublists(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("combat", {
        "stats": [{"id": "hp", "default": 10, "role": "resource_depletable"}],
        "abilities": [{"id": "firebolt", "name": "Firebolt"}],
        "combatants": [], "encounters": [], "statuses": [],
    })
    rows = asyncio.run(games.list_assets_game("g", "combat", user=U))
    assert {r["id"] for r in rows} == {"hp", "firebolt"}
    assert all(r["idkey"].startswith("combat:") for r in rows)


def test_edit_asset_generic_component_patches_one_item(tmp_path, monkeypatch):
    events = _events(monkeypatch)
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("characters", {"characters": [
        {"id": "mara", "name": "Mara", "role": "protagonist"},
        {"id": "jonas", "name": "Jonas", "role": "npc"}]})

    res = asyncio.run(games.edit_asset_game(
        "g", "characters", "mara", games.ComponentBody(content={"name": "Mara Voss"}), user=U))

    assert res["ok"] is True
    chars = state.read_component("characters")["characters"]
    mara = next(c for c in chars if c["id"] == "mara")
    assert mara["name"] == "Mara Voss" and mara["id"] == "mara"
    # jonas is untouched by a patch scoped to one item
    assert any(c["id"] == "jonas" and c["name"] == "Jonas" for c in chars)
    assert res["cleared_own_dirty"] is False
    assert any(e["type"] == "asset_updated" for e in events)


def test_edit_asset_404_for_unknown_item(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("characters", {"characters": [{"id": "mara", "name": "Mara"}]})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.edit_asset_game(
            "g", "characters", "ghost", games.ComponentBody(content={"name": "x"}), user=U))
    assert exc.value.status_code == 400


def test_edit_asset_node_clears_own_dirty_and_flags_dependents(tmp_path, monkeypatch):
    """The depgraph (Epic B) already knows n1 jumps to n2 — editing the REFERENCED node (n2)
    reflags its HOLDER (n1, which jumps there) dirty, even though n1 was never touched, and clears
    n2's own dirty flag (a hand-edit is itself a rewrite)."""
    events = _events(monkeypatch)
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("nodes", {"node_ids": ["n1", "n2"], "nodes": {
        "n1": {"lines": [{"speaker": None, "text": "old"}], "end": {"type": "jump", "target": "n2"}},
        "n2": {"lines": [{"speaker": None, "text": "hi"}], "end": {"type": "end"}},
    }})
    from maestro.modules.human import set_dirty, dirty_entries
    set_dirty(state, "nodes:n2", "fix this")

    res = asyncio.run(games.edit_asset_game(
        "g", "nodes", "n2", games.ComponentBody(
            content={"lines": [{"speaker": None, "text": "new"}], "end": {"type": "end"}}), user=U))

    assert res["ok"] is True
    assert res["cleared_own_dirty"] is True
    assert "nodes:n1" in res["flagged_dependents"]
    entries = {d["idkey"] for d in dirty_entries(state)}
    assert "nodes:n2" not in entries   # cleared
    assert "nodes:n1" in entries       # reflagged (it jumps into the edited node)
    assert any(e["type"] == "asset_dirty_set" and e.get("idkey") == "nodes:n1" for e in events)


def test_set_dirty_thumbs_up_thumbs_down(tmp_path, monkeypatch):
    events = _events(monkeypatch)
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    from maestro.modules.human import dirty_entries

    res = asyncio.run(games.set_dirty_game("g", games.DirtyBody(idkey="places:zone_crypt", note="too dark"),
                                           user=U))
    assert res == {"ok": True, "idkey": "places:zone_crypt", "note": "too dark"}
    assert dirty_entries(state)[0] == {"idkey": "places:zone_crypt", "note": "too dark"}
    assert any(e["type"] == "asset_dirty_set" for e in events)

    res = asyncio.run(games.thumbs_up_game("g", games.ThumbBody(idkey="places:zone_crypt"), user=U))
    assert res == {"ok": True, "idkey": "places:zone_crypt", "cleared": True}
    assert dirty_entries(state) == []
    assert any(e["type"] == "asset_dirty_cleared" for e in events)

    # thumbs-up on something that was never dirty is a harmless no-op, not a 404
    res = asyncio.run(games.thumbs_up_game("g", games.ThumbBody(idkey="places:zone_crypt"), user=U))
    assert res == {"ok": True, "idkey": "places:zone_crypt", "cleared": False}

    res = asyncio.run(games.thumbs_down_game(
        "g", games.DirtyBody(idkey="characters:mara", note="too flat"), user=U))
    assert res == {"ok": True, "idkey": "characters:mara", "note": "too flat"}
    assert dirty_entries(state)[0]["idkey"] == "characters:mara"


def test_list_and_get_asset_manifest_carry_run_id_for_the_viewer(tmp_path, monkeypatch):
    """asset_manifest is the one component the browser needs a run id on (to build asset-file
    URLs and call regenerate-asset without prop-threading runId through the component-blind
    shell) — injected only for this component, so the generic per-item shape (test above) is
    untouched for every other component."""
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("asset_manifest", {"backgrounds": [], "characters": [], "cgs": []})

    rows = asyncio.run(games.list_assets_game("g", "asset_manifest", user=U))
    assert rows[0]["run_id"] == "g"

    row = asyncio.run(games.get_asset_game("g", "asset_manifest", "asset_manifest", user=U))
    assert row["run_id"] == "g"


# ── per-asset regenerate + file serving (asset viewer) ────────────────────────────────────────
def test_regenerate_asset_passes_presentation_and_filename(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_spec({**state.read_spec(), "presentation": "hd2d"})
    calls = {}

    def fake_generate_single_asset(inputs, run_dir, filename, presentation="2d"):
        calls["filename"] = filename
        calls["presentation"] = presentation
        return {"status": "ok", "generated": [filename], "failed": []}

    monkeypatch.setattr("renpy.fns.generate_single_asset", fake_generate_single_asset)

    res = asyncio.run(games.regenerate_asset_game("g", games.AssetRegenBody(filename="bg_x.png"),
                                                  user=U))

    assert res == {"status": "ok", "generated": ["bg_x.png"], "failed": []}
    assert calls == {"filename": "bg_x.png", "presentation": "hd2d"}


def test_regenerate_asset_error_result_is_400(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    monkeypatch.setattr("renpy.fns.generate_single_asset",
                        lambda *a, **k: {"status": "error", "error": "unknown asset filename 'x.png'"})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.regenerate_asset_game("g", games.AssetRegenBody(filename="x.png"), user=U))
    assert exc.value.status_code == 400


def test_regenerate_asset_blocked_while_building_unpaused(tmp_path, monkeypatch):
    """Same gate as regenerate-assets/compile — races the executor thread on the same files."""
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    build_queue._current = "g"
    run_control.get_or_create("g")
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(games.regenerate_asset_game("g", games.AssetRegenBody(filename="x.png"),
                                                    user=U))
        assert exc.value.status_code == 409
    finally:
        build_queue._current = None
        run_control.remove("g")


def test_asset_file_serves_bytes_from_the_run_images_dir(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    images_dir = state.run_dir / "game_output" / "game" / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "bg_x.png").write_bytes(b"\x89PNG fake bytes")

    resp = asyncio.run(games.asset_file_game("g", "bg_x.png", user=U))

    assert isinstance(resp, FileResponse)
    assert Path(resp.path).read_bytes() == b"\x89PNG fake bytes"


def test_asset_file_404_for_missing_file(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    (state.run_dir / "game_output" / "game" / "images").mkdir(parents=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.asset_file_game("g", "nope.png", user=U))
    assert exc.value.status_code == 404


def test_asset_file_rejects_path_traversal(tmp_path, monkeypatch):
    """A `../` filename must never escape the run's images dir — even though FastAPI's plain
    `{filename}` path param already refuses to match a literal '/', a bare '..' alone (no slash)
    still resolves up a directory, so the handler re-checks with is_relative_to."""
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    (state.run_dir / "game_output" / "game" / "images").mkdir(parents=True)
    secret = state.run_dir / "game_output" / "secret.json"
    secret.write_text("{}")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.asset_file_game("g", "..", user=U))
    assert exc.value.status_code == 403


def test_edit_place_uses_write_place_and_bypasses_lock(tmp_path, monkeypatch):
    """places has no per-item write tool other than write_place; the uniform edit path must be
    able to overwrite an EXISTING (locked-or-not) place — write_place needed a `force` escape
    hatch (Epic C3) since it had none before."""
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path, modules=["world"])
    state.write_component("places", {"place_ids": ["zone_crypt"], "places": {
        "zone_crypt": {"kind": "room", "background": "bg1",
                       "interactables": [{"id": "door", "action": {"type": "win"}}]}}})

    res = asyncio.run(games.edit_asset_game(
        "g", "places", "zone_crypt", games.ComponentBody(content={
            "kind": "room", "background": "bg2",
            "interactables": [{"id": "door", "action": {"type": "win"}}]}), user=U))

    assert res["ok"] is True
    assert state.read_component("places")["places"]["zone_crypt"]["background"] == "bg2"
