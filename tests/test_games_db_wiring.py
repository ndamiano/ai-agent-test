"""The games API against the platform db: list/detail read db rows, ownership gates cross-user
access, and the build endpoint charges once — credits deducted, seconds granted, re-enqueue free."""

import json

import pytest

from maestro.codegen import build_chain
from maestro.codegen.staging import game_dir
from auth import store as auth_store
from auth.billing import SECONDS_PER_CREDIT
from db import store as db_store
from db.estimates import estimate_seconds
from maestro.codegen.run import create_run
from maestro.state import RunState


@pytest.fixture
def client(app_client, tmp_runs):
    return app_client


def _user(handle="alice", credits=10):
    u = auth_store.create_user(handle, "pw")
    if credits:
        auth_store.grant(u.id, credits, "admin_grant")
    return u, {"Authorization": f"Bearer {auth_store.issue_token(u.id)}"}


def _make_game(user_id, spec):
    run_id = create_run(user_id)
    RunState(run_id).write_spec(spec)
    db_store.update_prompt_meta(run_id, spec.get("title", ""))
    return run_id


def test_list_and_detail_come_from_the_db(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})

    (row,) = client.get("/api/games", headers=headers).json()
    assert (row["run_id"], row["title"]) == (run_id, "Moon Miner")

    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert (detail["title"], detail["prompt"]) == ("Moon Miner", "make a moon miner")
    assert detail["credits_spent"] == 0
    assert detail["budget_pct_remaining"] is None   # uncharged → no bar


def test_new_game_creates_the_run_from_the_prompt_and_builds(client, monkeypatch):
    user, headers = _user()
    started = []
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: started.append(rid) or "bid")

    r = client.post("/api/games", headers=headers,
                    json={"prompt": "an open world RPG with card combat"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert started == [run_id]
    assert RunState(run_id).read_spec()["request"] == "an open world RPG with card combat"
    assert db_store.owner_of(run_id) == user.id
    assert auth_store.balance(user.id) == 9
    assert db_store.game(run_id)["seconds_granted"] == SECONDS_PER_CREDIT


def test_new_game_with_no_credits_is_402_and_creates_nothing(client, monkeypatch):
    """The balance is checked BEFORE the run exists, so a broke user leaves no orphan run."""
    user, headers = _user(credits=0)
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: "bid")

    r = client.post("/api/games", headers=headers, json={"prompt": "a maze game"})
    assert r.status_code == 402
    assert client.get("/api/games", headers=headers).json() == []


def test_new_game_with_an_empty_prompt_is_400(client):
    _, headers = _user()
    assert client.post("/api/games", headers=headers,
                       json={"prompt": "   "}).status_code == 400
    assert client.get("/api/games", headers=headers).json() == []


def test_build_stores_the_edited_prompt_it_was_given(client, monkeypatch):
    """Pressing Build is what approves the text, so the build call is the only writer."""
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "a maze game", "title": "Maze"})
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: "bid")

    r = client.post(f"/api/games/{run_id}/build", headers=headers,
                    json={"prompt": "a maze game with a boss fight"})
    assert r.status_code == 200
    assert RunState(run_id).read_spec()["request"] == "a maze game with a boss fight"
    assert client.get(f"/api/games/{run_id}", headers=headers).json()["prompt"] == \
        "a maze game with a boss fight"


def test_build_without_a_prompt_keeps_what_is_on_disk(client, monkeypatch):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "a maze game", "title": "Maze"})
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: "bid")

    assert client.post(f"/api/games/{run_id}/build", headers=headers).status_code == 200
    assert RunState(run_id).read_spec()["request"] == "a maze game"


def test_building_an_empty_prompt_is_400_and_changes_nothing(client, monkeypatch):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "a maze game", "title": "Maze"})
    started = []
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: started.append(rid))

    assert client.post(f"/api/games/{run_id}/build", headers=headers,
                       json={"prompt": "  "}).status_code == 400
    assert RunState(run_id).read_spec()["request"] == "a maze game"
    assert started == []
    assert auth_store.balance(user.id) == 10   # a refused prompt never charges


def test_cross_user_access_is_403(client):
    user, _ = _user("alice")
    _, other_headers = _user("bob")
    run_id = _make_game(user.id, {"request": "make a mine", "title": "Mine"})
    assert client.get(f"/api/games/{run_id}", headers=other_headers).status_code == 403
    assert client.get("/api/games", headers=other_headers).json() == []


def test_build_charges_once_and_grants_seconds(client, monkeypatch):

    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})

    # Kick off without running a real build (no worker to drive completions in tests).
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: "bid")

    r = client.post(f"/api/games/{run_id}/build", headers=headers)
    assert r.status_code == 200
    assert auth_store.balance(user.id) == 9
    row = db_store.game(run_id)
    assert row["credits_spent"] == 1
    assert row["seconds_granted"] == SECONDS_PER_CREDIT

    # Second enqueue: already charged — no second deduction, no second grant.
    client.post(f"/api/games/{run_id}/build", headers=headers)
    assert auth_store.balance(user.id) == 9
    assert db_store.game(run_id)["seconds_granted"] == SECONDS_PER_CREDIT


def test_build_with_no_credits_is_402_and_uncharged(client, monkeypatch):
    user, headers = _user(credits=0)
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})

    r = client.post(f"/api/games/{run_id}/build", headers=headers)
    assert r.status_code == 402
    assert not db_store.is_charged(run_id)


def test_a_game_out_of_compute_is_402_on_every_gpu_endpoint(client, monkeypatch):
    """Credits buy a grant ONCE; the grant is what each later build/fix/skin spends. A game that
    has burned it must be refused before it takes the GPU slot, not after."""
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: "bid")

    assert client.post(f"/api/games/{run_id}/build", headers=headers).status_code == 200
    db_store.add_seconds_used(run_id, SECONDS_PER_CREDIT)

    for path, body in (("build", None), ("fix", {"note": "a"}), ("assets", None), ("resume", None)):
        r = client.post(f"/api/games/{run_id}/{path}", headers=headers, json=body)
        assert r.status_code == 402, path
        assert r.json()["detail"]["reason"] == "compute_exhausted"

    # Still charged — a refusal is not a refund, and credits are not re-deducted on retry.
    assert auth_store.balance(user.id) == 9
    assert db_store.game(run_id)["credits_spent"] == 1


def test_detail_reports_remaining_net_of_queued_work(client):
    """The budget bar nets out queued work immediately: seconds_used lags by the whole depth of the
    queue, so a bar drawn from spend alone would read full while a build's jobs are already spoken
    for. budget_pct_remaining is computed from compute_remaining, which reserves the estimate."""
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    db_store.charge_game(run_id, 1, 1000.0)
    db_store.enqueue_job("mesh", {}, game_id=run_id)

    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert detail["budget_pct_remaining"] == (1000.0 - estimate_seconds("mesh")) / 1000.0


def test_events_endpoint_replays_the_log(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    db_store.record_event(run_id, "prompt_proposed", {"title": "Moon Miner"})
    db_store.record_event(run_id, "build_step", {"step": 1})

    events = client.get(f"/api/games/{run_id}/events", headers=headers).json()
    assert [e["kind"] for e in events] == ["prompt_proposed", "build_step"]
    after = events[0]["id"]
    later = client.get(f"/api/games/{run_id}/events?after={after}", headers=headers).json()
    assert [e["kind"] for e in later] == ["build_step"]


def _write_assets(run_id, manifest, files=()):
    gd = game_dir(RunState(run_id).run_dir)
    (gd / "assets").mkdir(parents=True, exist_ok=True)
    (gd / "assets.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, data in files:
        (gd / "assets" / name).write_bytes(data)


def test_assets_empty_before_the_game_declares_any(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    assert client.get(f"/api/games/{run_id}/assets", headers=headers).json() == []


def test_assets_report_per_asset_status(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    # hero.webp is on disk (rendered), slime is planned but not yet rendered → pending.
    _write_assets(run_id,
                  {"images": [{"id": "hero", "file": "assets/hero.webp", "prompt": "a hero"},
                              {"id": "slime", "file": "assets/slime.webp", "prompt": "a slime"}]},
                  files=[("hero.webp", b"WEBPDATA")])

    assets = client.get(f"/api/games/{run_id}/assets", headers=headers).json()
    by_id = {a["id"]: a for a in assets}
    assert by_id["hero"] == {"id": "hero", "kind": "sprite", "status": "ready",
                             "prompt": "a hero", "defect": None}
    assert by_id["slime"]["status"] == "pending"


def test_asset_blob_streams_and_guards(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    _write_assets(run_id, {"images": [{"id": "hero", "file": "assets/hero.webp",
                                      "prompt": "a hero"}]},
                  files=[("hero.webp", b"WEBPDATA")])

    ok = client.get(f"/api/games/{run_id}/assets/hero", headers=headers)
    assert ok.status_code == 200 and ok.content == b"WEBPDATA"
    assert ok.headers["content-type"] == "image/webp"

    assert client.get(f"/api/games/{run_id}/assets/nope", headers=headers).status_code == 404
    assert client.get(f"/api/games/{run_id}/assets/bad!id", headers=headers).status_code == 400

    _, other = _user("bob")
    assert client.get(f"/api/games/{run_id}/assets/hero", headers=other).status_code == 403
    assert client.get(f"/api/games/{run_id}/assets", headers=other).status_code == 403


def test_regenerate_enqueues_one_image_job_with_the_new_prompt(client, monkeypatch):
    """A single-asset regen puts ONE image job on the queue carrying the merged prompt (in the
    comfy workflow) + the asset_id/then in metadata — the batch's finalize saves and re-stages it.
    No whole-game re-render."""
    import maestro.codegen.assets as assets_mod

    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    _write_assets(run_id, {"images": [{"id": "hero", "file": "assets/hero.webp",
                                      "prompt": "a hero"}]})
    db_store.charge_game(run_id, 1, 10_000.0)   # grant compute so the enqueue is admitted
    # The merge is an LLM call; the note-vs-original contract is tested on _merge_prompt itself.
    monkeypatch.setattr(assets_mod, "_merge_prompt",
                        lambda original, note: "a brave knight, pixel art")

    r = client.post(f"/api/games/{run_id}/assets/hero/regenerate", headers=headers,
                    json={"prompt": "make him a knight"})
    assert r.status_code == 200
    assert r.json() == {"status": "regenerating", "run_id": run_id, "asset_id": "hero"}

    job = db_store.claim_job("image", "w1", 60)
    assert job is not None and job["game_id"] == run_id and job["batch_id"]
    assert job["payload"]["workflow"]["6"]["inputs"]["text"] == "a brave knight, pixel art"
    meta = json.loads(job["metadata"])
    assert meta["asset_id"] == "hero"
    assert meta["then"] == {"operations": ["save_sprite"], "finalize": "assets"}


def test_regenerate_merge_is_attributed_to_the_game(client, monkeypatch):
    """The prompt merge is a real GPU job, and the only enqueue here that goes through the blocking
    connector — which reads the owning game off the run scope. Outside that scope it lands with no
    game_id, so it is neither metered nor gated by the budget it is spending."""
    import maestro.codegen.assets as assets_mod
    from tools.execution_context import get_run_id

    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    _write_assets(run_id, {"images": [{"id": "hero", "file": "assets/hero.webp",
                                      "prompt": "a hero"}]})
    db_store.charge_game(run_id, 1, 10_000.0)
    seen = {}

    def _merge(original, note):
        seen["run_id"] = get_run_id()
        return "merged"

    monkeypatch.setattr(assets_mod, "_merge_prompt", _merge)

    r = client.post(f"/api/games/{run_id}/assets/hero/regenerate", headers=headers,
                    json={"prompt": "make him a knight"})
    assert r.status_code == 200
    assert seen["run_id"] == run_id


def test_regenerate_cross_user_is_403(client):
    user, _ = _user("alice")
    _, other = _user("bob")
    run_id = _make_game(user.id, {"request": "make a mine", "title": "Mine"})
    db_store.charge_game(run_id, 1, 10_000.0)
    r = client.post(f"/api/games/{run_id}/assets/hero/regenerate", headers=other,
                    json={"prompt": "x"})
    assert r.status_code == 403


def test_regenerate_bad_asset_id_is_400(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    db_store.charge_game(run_id, 1, 10_000.0)
    r = client.post(f"/api/games/{run_id}/assets/bad!id/regenerate", headers=headers,
                    json={"prompt": "x"})
    assert r.status_code == 400


def test_enhance_charges_the_credit_and_saves_the_plan(client, monkeypatch):
    from maestro.codegen import stages as stage_plan
    user, headers = _user()
    started = []
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: started.append(rid) or "bid")
    monkeypatch.setattr(stage_plan, "plan",
                        lambda text, rid: ["build the duel alone", "add the world"])

    r = client.post("/api/games/enhance", headers=headers, json={"prompt": "card rpg for ante"})
    assert r.status_code == 200
    body = r.json()
    assert body["stages"] == ["build the duel alone", "add the world"]
    assert started == []                                        # nothing built yet
    assert auth_store.balance(user.id) == 9                     # planning IS the charge
    assert db_store.is_charged(body["run_id"])
    saved = stage_plan.saved(RunState(body["run_id"]).run_dir)
    assert saved["stages"] == body["stages"]                    # resumable from disk

    # The build that follows never charges again.
    client.post(f"/api/games/{body['run_id']}/build", headers=headers,
                json={"stages": body["stages"]})
    assert auth_store.balance(user.id) == 9


def test_replanning_a_paid_run_charges_nothing(client, monkeypatch):
    from maestro.codegen import stages as stage_plan
    user, headers = _user()
    monkeypatch.setattr(stage_plan, "plan", lambda text, rid: ["one", "two"])
    first = client.post("/api/games/enhance", headers=headers,
                        json={"prompt": "card rpg"}).json()

    monkeypatch.setattr(stage_plan, "plan", lambda text, rid: ["duel first", "then world"])
    again = client.post("/api/games/enhance", headers=headers,
                        json={"prompt": "card rpg, but cozier", "run_id": first["run_id"]})
    assert again.status_code == 200
    assert again.json()["run_id"] == first["run_id"]
    assert again.json()["stages"] == ["duel first", "then world"]
    assert auth_store.balance(user.id) == 9                     # one credit total
    assert RunState(first["run_id"]).read_spec()["request"] == "card rpg, but cozier"


def test_enhance_with_no_credits_is_402_and_creates_nothing(client, monkeypatch):
    user, headers = _user(credits=0)
    r = client.post("/api/games/enhance", headers=headers, json={"prompt": "a maze game"})
    assert r.status_code == 402
    assert client.get("/api/games", headers=headers).json() == []


def test_an_unbuilt_plan_is_offered_for_resume(client, monkeypatch):
    from maestro.codegen import stages as stage_plan
    user, headers = _user()
    monkeypatch.setattr(stage_plan, "plan", lambda text, rid: ["one", "two"])
    run_id = client.post("/api/games/enhance", headers=headers,
                         json={"prompt": "card rpg"}).json()["run_id"]

    (row,) = client.get("/api/games", headers=headers).json()
    assert row["unstarted_plan"] is True
    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert detail["plan"] == ["one", "two"]

    # Once Build is pressed, it stops being offered — a pressed build leaves a builds row.
    monkeypatch.setattr(build_chain, "kickoff",
                        lambda rid, **kw: db_store.create_build(rid))
    client.post(f"/api/games/{run_id}/build", headers=headers, json={"stages": ["one", "two"]})
    (row,) = client.get("/api/games", headers=headers).json()
    assert row["unstarted_plan"] is False


def test_staged_build_stores_plan_and_builds_stage_one(client, monkeypatch):
    from maestro.codegen import stages as stage_plan
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "card rpg for ante", "title": "Card RPG"})
    started = []
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: started.append(kw) or "bid")

    r = client.post(f"/api/games/{run_id}/build", headers=headers,
                    json={"stages": ["the duel alone", "add the world"]})
    assert r.status_code == 200
    assert len(started) == 1
    assert RunState(run_id).read_spec()["request"] == "the duel alone"
    saved = json.loads((RunState(run_id).run_dir / stage_plan.STATE_FILE).read_text())
    assert saved["stages"] == ["the duel alone", "add the world"]
    assert saved["request"] == "card rpg for ante"              # the original survives the plan


def test_plain_build_clears_a_stale_stage_plan(client, monkeypatch):
    from maestro.codegen import stages as stage_plan
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "card rpg", "title": "Card RPG"})
    stage_plan.save(RunState(run_id).run_dir, "card rpg", ["one", "two"])
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: "bid")

    r = client.post(f"/api/games/{run_id}/build", headers=headers, json={"prompt": "card rpg"})
    assert r.status_code == 200
    assert not (RunState(run_id).run_dir / stage_plan.STATE_FILE).exists()


def test_regenerate_out_of_compute_is_402(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "make a moon miner", "title": "Moon Miner"})
    db_store.charge_game(run_id, 1, SECONDS_PER_CREDIT)
    db_store.add_seconds_used(run_id, SECONDS_PER_CREDIT)   # burn the whole grant
    r = client.post(f"/api/games/{run_id}/assets/hero/regenerate", headers=headers,
                    json={"prompt": "x"})
    assert r.status_code == 402
    assert r.json()["detail"]["reason"] == "compute_exhausted"


def test_a_mid_chain_run_reports_building_not_built(client, monkeypatch, tmp_path):
    """Every stage finalize stages a playable game, but the chain is the build — no play button
    between stages."""
    from maestro.codegen import stages as stage_plan
    user, headers = _user()
    run_id = _make_game(user.id, {"request": "a fox game", "title": "Fox"})
    stage_plan.save(RunState(run_id).run_dir, "a fox game", ["core", "world", "polish"])
    db_store.create_build(run_id)                  # build was pressed
    stage_plan.next_note(RunState(run_id).run_dir)  # stage 2 consumed -> next=2, one stage left
    game = game_dir(RunState(run_id).run_dir)
    game.mkdir(parents=True, exist_ok=True)
    (game / "index.html").write_text("<html>stage 2 of 3</html>")
    from maestro.codegen.staging import stage_for_play
    stage_for_play(RunState(run_id).run_dir, run_id)

    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert detail["status"] == "building"
    assert detail["built"] is False and detail["building"] is True

    (row,) = client.get("/api/games", headers=headers).json()
    assert row["built"] is False and row["building"] is True

    # The chain finishing (next == len) is what makes it built.
    stage_plan.next_note(RunState(run_id).run_dir)
    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert detail["status"] == "built" and detail["built"] is True
