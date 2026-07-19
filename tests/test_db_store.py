"""Platform db — games ownership/lifecycle, the charge contract, builds, and the event log."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")


# ── games ─────────────────────────────────────────────────────────────────────
def test_create_and_owner():
    store.create_game("g1", "u1")
    assert store.owner_of("g1") == "u1"
    assert store.owner_of("ghost") is None
    row = store.game("g1")
    assert row["status"] == "draft"
    assert row["credits_spent"] == 0


def test_list_is_per_user():
    store.create_game("g1", "u1")
    store.create_game("g2", "u2")
    assert [g["id"] for g in store.list_games("u1")] == ["g1"]


def test_spec_meta_mirrors_title_mode_and_frozen():
    store.create_game("g1", "u1")
    store.update_spec_meta("g1", "Moon Miner", "3d", frozen=False)
    row = store.game("g1")
    assert (row["title"], row["mode"], row["status"]) == ("Moon Miner", "3d", "draft")
    store.update_spec_meta("g1", "Moon Miner", "3d", frozen=True)
    assert store.game("g1")["status"] == "frozen"


def test_charge_grants_seconds_and_is_durable():
    store.create_game("g1", "u1")
    assert not store.is_charged("g1")
    store.charge_game("g1", 1, 14_400)
    assert store.is_charged("g1")
    row = store.game("g1")
    assert row["credits_spent"] == 1
    assert row["seconds_granted"] == 14_400
    # An extension stacks; it never resets.
    store.charge_game("g1", 1, 14_400)
    row = store.game("g1")
    assert (row["credits_spent"], row["seconds_granted"]) == (2, 28_800)


def test_seconds_used_accumulates():
    store.create_game("g1", "u1")
    store.add_seconds_used("g1", 12.5)
    store.add_seconds_used("g1", 7.5)
    assert store.game("g1")["seconds_used"] == 20.0


# ── builds ────────────────────────────────────────────────────────────────────
def test_build_lifecycle():
    store.create_game("g1", "u1")
    bid = store.create_build("g1", kind="build")
    store.build_started(bid)
    store.build_finished(bid, "succeeded", steps=7)
    (b,) = store.builds_for("g1")
    assert (b["id"], b["kind"], b["status"], b["steps"]) == (bid, "build", "succeeded", 7)
    assert b["queued_at"] <= b["started_at"] <= b["finished_at"]


def test_a_game_accumulates_builds():
    store.create_game("g1", "u1")
    store.create_build("g1", kind="build")
    store.create_build("g1", kind="fix")
    store.create_build("g1", kind="assets")
    assert [b["kind"] for b in store.builds_for("g1")] == ["build", "fix", "assets"]


# ── events ────────────────────────────────────────────────────────────────────
def test_events_append_and_replay_after_id():
    store.create_game("g1", "u1")
    store.record_event("g1", "spec_proposed", {"title": "Moon Miner"})
    store.record_event("g1", "build_step", {"step": 1})
    store.record_event("g2", "build_step", {"step": 9})   # another game — never mixed in

    events = store.events_for("g1")
    assert [e["kind"] for e in events] == ["spec_proposed", "build_step"]
    assert events[0]["payload"] == {"title": "Moon Miner"}

    later = store.events_for("g1", after_id=events[0]["id"])
    assert [e["kind"] for e in later] == ["build_step"]


def test_event_payload_survives_non_json_values():
    store.create_game("g1", "u1")
    store.record_event("g1", "weird", {"path": Path("/tmp/x")})   # default=str, never raises
    (e,) = store.events_for("g1")
    assert e["payload"]["path"] == "/tmp/x"
