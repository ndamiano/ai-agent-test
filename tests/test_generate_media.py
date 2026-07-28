"""`generate_media` — the tool the game asks for its own art with.

What is pinned here is the ANSWER, not the render: the model gets the path the file will live at
while the render is still queued, and writes code against it. A bad id, a blocked prompt and an
exhausted budget all come back as text instead, because a build that cannot have art has to be told
to draw one rather than left waiting for a file that is never coming.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import maestro.state
from db import store
from maestro.codegen import assets
from maestro.codegen.assets import read_manifest, request_media
from maestro.codegen.tools import build_tools
from maestro.state import RunState

RUN = "r1"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)
    store.create_game(RUN, "u1")
    store.charge_game(RUN, 1, 10_000.0)


@pytest.fixture
def run_dir(tmp_path):
    d = RunState(RUN).run_dir
    (d / "game").mkdir(parents=True, exist_ok=True)
    return d


def _image_jobs():
    with store._db() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE queue = 'image' ORDER BY id").fetchall()
    return [store._job_dict(r) for r in rows]


# ── the answer ────────────────────────────────────────────────────────────────
def test_the_path_comes_back_before_the_render(run_dir):
    out = request_media(RUN, run_dir, "goblin", "a snarling goblin")
    assert out == {"ok": True, "path": "assets/goblin.png", "status": "rendering"}
    assert len(_image_jobs()) == 1


def test_a_mesh_answers_with_a_glb_path_and_chains_trellis(run_dir):
    out = request_media(RUN, run_dir, "hut", "a thatched hut", kind="mesh")
    assert out["path"] == "assets/hut.glb"
    md = _image_jobs()[0]["metadata"]
    assert md["kind"] == "mesh"
    assert md["then"]["enqueue"] == "mesh_from_image"


def test_the_request_is_recorded_in_the_manifest(run_dir):
    """The model never writes assets.json — this file is the platform's record of what was asked
    for, and it is what the gallery, the top-up and the regenerate all read."""
    request_media(RUN, run_dir, "goblin", "a snarling goblin")
    request_media(RUN, run_dir, "hut", "a thatched hut", kind="mesh")
    assert read_manifest(run_dir) == [
        {"id": "goblin", "file": "assets/goblin.png", "prompt": "a snarling goblin"},
        {"id": "hut", "file": "assets/hut.glb", "prompt": "a thatched hut", "kind": "mesh"},
    ]


def test_the_batch_carries_no_build_id(run_dir):
    """Its finalize calls build_finished. Handed the BUILD's id, the first sprite that lands would
    close the build row while the model is still writing the game."""
    request_media(RUN, run_dir, "goblin", "a snarling goblin")
    assert _image_jobs()[0]["build_id"] is None


def test_each_request_is_its_own_batch(run_dir):
    """One asset per batch, so each finalize re-stages as it lands instead of waiting on the
    slowest render in a set."""
    request_media(RUN, run_dir, "goblin", "a goblin")
    request_media(RUN, run_dir, "hut", "a hut")
    batches = {j["batch_id"] for j in _image_jobs()}
    assert len(batches) == 2 and None not in batches


# ── asking twice ──────────────────────────────────────────────────────────────
def test_the_same_id_twice_is_one_render(run_dir):
    first = request_media(RUN, run_dir, "goblin", "a snarling goblin")
    again = request_media(RUN, run_dir, "goblin", "a snarling goblin, but bigger")
    assert again == first
    assert len(_image_jobs()) == 1
    assert len(read_manifest(run_dir)) == 1


def test_an_already_rendered_asset_is_not_paid_for_again(run_dir):
    """A fix or a resumed build re-runs the same code path over a folder that already has its art."""
    (run_dir / "game" / "assets").mkdir(parents=True, exist_ok=True)
    (run_dir / "game" / "assets" / "goblin.png").write_bytes(b"\x89PNG")
    out = request_media(RUN, run_dir, "goblin", "a snarling goblin")
    assert out == {"ok": True, "path": "assets/goblin.png", "status": "ready"}
    assert _image_jobs() == []


# ── every refusal is reported, never guessed at ───────────────────────────────
@pytest.mark.parametrize("bad", ["", "../escape", "a b", "x" * 65, "a/b"])
def test_an_id_that_cannot_name_a_file_is_refused(run_dir, bad):
    out = request_media(RUN, run_dir, bad, "a goblin")
    assert out["ok"] is False and "id" in out["error"]
    assert _image_jobs() == []


def test_a_missing_prompt_is_refused(run_dir):
    out = request_media(RUN, run_dir, "goblin", "   ")
    assert out["ok"] is False and "prompt" in out["error"]


def test_an_unknown_kind_is_refused(run_dir):
    out = request_media(RUN, run_dir, "goblin", "a goblin", kind="video")
    assert out["ok"] is False and "video" in out["error"]


def test_a_blocked_prompt_tells_the_model_to_draw_it_instead(run_dir, monkeypatch):
    monkeypatch.setattr(assets, "build_item_payload", lambda *a, **kw: None)
    out = request_media(RUN, run_dir, "goblin", "something refused")
    assert out["ok"] is False and "draw this one with code" in out["error"]
    assert read_manifest(run_dir) == []


def test_an_exhausted_budget_tells_the_model_to_draw_it_instead(run_dir):
    """The compute budget is the only cap on how much art a build may ask for — there is no call
    limit, because a refused enqueue already says so in words the model can act on."""
    store.add_seconds_used(RUN, 10_000.0)
    out = request_media(RUN, run_dir, "goblin", "a goblin")
    assert out["ok"] is False and "draw this one with code" in out["error"]
    assert _image_jobs() == []
    assert read_manifest(run_dir) == []


# ── the tool surface ──────────────────────────────────────────────────────────
def test_the_tool_reaches_the_queue_and_defaults_to_an_image(run_dir):
    tools = build_tools(RunState(RUN))
    out = tools["generate_media"](id="goblin", prompt="a snarling goblin")
    assert out == {"ok": True, "path": "assets/goblin.png", "status": "rendering"}
    assert _image_jobs()[0]["metadata"]["kind"] == "image"


def test_the_tool_reports_a_missing_argument_rather_than_guessing(run_dir):
    tools = build_tools(RunState(RUN))
    assert tools["generate_media"](prompt="a goblin")["ok"] is False
    assert tools["generate_media"](id="goblin")["ok"] is False
    assert _image_jobs() == []
