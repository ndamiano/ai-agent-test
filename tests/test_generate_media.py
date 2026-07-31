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


def test_the_same_id_asked_twice_is_answered_not_obeyed(run_dir):
    """The first repeat costs nothing — an id asked for twice is usually the model losing track."""
    request_media(RUN, run_dir, "goblin", "a snarling goblin")
    again = request_media(RUN, run_dir, "goblin", "a snarling goblin, but bigger")

    assert "not requeued" in again["note"]
    assert len(_image_jobs()) == 1
    assert len(read_manifest(run_dir)) == 1
    assert read_manifest(run_dir)[0]["prompt"] == "a snarling goblin"


def test_a_confirmed_repeat_replaces_the_art_and_the_prompt(run_dir):
    """Asking again after the answer is the model meaning it — which is how a playtest note that
    says redraw these reaches the renderer at all."""
    request_media(RUN, run_dir, "goblin", "a snarling goblin")
    request_media(RUN, run_dir, "goblin", "a goblin, full body, for a 3d model")
    out = request_media(RUN, run_dir, "goblin", "a goblin, full body, for a 3d model")

    assert out == {"ok": True, "path": "assets/goblin.png", "status": "rendering"}
    assert len(_image_jobs()) == 2
    entries = read_manifest(run_dir)
    assert len(entries) == 1, "a replacement rewrites its record rather than adding one"
    assert entries[0]["prompt"] == "a goblin, full body, for a 3d model"
    assert "replace_asked" not in entries[0]


def test_a_replacement_is_answered_again_before_the_next_one(run_dir):
    """The flag clears with the render it authorized, so the turn after does not requeue blind."""
    request_media(RUN, run_dir, "goblin", "a goblin")
    request_media(RUN, run_dir, "goblin", "a bigger goblin")
    request_media(RUN, run_dir, "goblin", "a bigger goblin")
    again = request_media(RUN, run_dir, "goblin", "a bigger goblin still")

    assert "not requeued" in again["note"]
    assert len(_image_jobs()) == 2


def test_an_already_rendered_asset_is_not_paid_for_again(run_dir):
    """A fix or a resumed build re-runs the same code path over a folder that already has its art."""
    (run_dir / "game" / "assets").mkdir(parents=True, exist_ok=True)
    (run_dir / "game" / "assets" / "goblin.png").write_bytes(b"\x89PNG")
    out = request_media(RUN, run_dir, "goblin", "a snarling goblin")
    assert out == {"ok": True, "path": "assets/goblin.png", "status": "ready"}
    assert _image_jobs() == []


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


def _jobs(queue):
    with store._db() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE queue = ? ORDER BY id", (queue,)).fetchall()
    return [store._job_dict(r) for r in rows]


def test_a_top_up_resumes_a_mesh_from_the_source_render_it_already_has(run_dir):
    """A mesh is ComfyUI and then TRELLIS, and one GPU holds one of them, so a top-up that always
    restarted at the image leg could never reach the second half on a single-card box."""
    request_media(RUN, run_dir, "hut", "a thatched hut", kind="mesh")
    src = assets.asset_path(RUN, "hut", "src.png")
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x89PNG-the-render-we-already-paid-for")

    assets.start_from_manifest(RUN, run_dir)

    mesh_jobs = _jobs("mesh")
    assert len(mesh_jobs) == 1, "the top-up went to TRELLIS, not back to ComfyUI"
    assert mesh_jobs[0]["payload"]["kind"] == "trellis_mesh"
    assert mesh_jobs[0]["metadata"]["then"]["operations"] == ["decimate"]
    assert len(_jobs("image")) == 1, "no second image render was paid for"


def test_a_mesh_with_no_source_render_still_starts_at_the_image(run_dir):
    request_media(RUN, run_dir, "hut", "a thatched hut", kind="mesh")

    assets.start_from_manifest(RUN, run_dir)

    assert len(_jobs("image")) == 2 and _jobs("mesh") == []
