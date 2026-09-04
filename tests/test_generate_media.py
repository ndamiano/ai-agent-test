import json
"""`generate_media` — the tool the game asks for its own art with.

What is pinned here is the ANSWER, not the render: the model gets the path the file will live at
while the render is still queued, and writes code against it. A bad id, a blocked prompt and an
exhausted budget all come back as text instead, because a build that cannot have art has to be told
to draw one rather than left waiting for a file that is never coming.
"""

import pytest

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
    assert (out["ok"], out["path"], out["status"]) == (True, "assets/goblin.webp", "rendering")
    assert "stand-in" in out["note"]
    assert len(_image_jobs()) == 1


def test_a_mesh_answers_with_a_glb_path_and_chains_trellis(run_dir):
    out = request_media(RUN, run_dir, "hut", "a thatched hut", kind="mesh")
    assert out["path"] == "assets/hut.glb"
    # The one fact placement code cannot discover: the mesh arrives normalized to 1 unit.
    assert "1 unit" in out["note"]
    md = _image_jobs()[0]["metadata"]
    assert md["kind"] == "mesh"
    assert md["then"]["enqueue"] == "mesh_from_image"


@pytest.mark.parametrize("kind,op", [("sprite", "save_sprite"), ("tile", "save_flat"),
                                     ("scene", "save_flat")])
def test_only_a_sprite_is_matted_and_cropped(run_dir, kind, op):
    """A tile and a scene ARE the background — matting one leaves the ragged fragments of a floor
    that used to be a floor, and cropping to the "subject" finishes the job."""
    request_media(RUN, run_dir, "art", "some art", kind=kind)
    md = _image_jobs()[0]["metadata"]
    assert md["kind"] == kind
    assert md["then"]["operations"] == [op]


def test_the_request_is_recorded_in_the_manifest(run_dir):
    """The model never writes assets.json — this file is the platform's record of what was asked
    for, and it is what the gallery, the top-up and the regenerate all read."""
    request_media(RUN, run_dir, "goblin", "a snarling goblin")
    request_media(RUN, run_dir, "hut", "a thatched hut", kind="mesh")
    assert read_manifest(run_dir) == [
        {"id": "goblin", "file": "assets/goblin.webp", "kind": "sprite",
         "prompt": "a snarling goblin", "placeholder": True},
        {"id": "hut", "file": "assets/hut.glb", "kind": "mesh", "prompt": "a thatched hut",
         "placeholder": True},
    ]


def test_a_placeholder_is_at_the_path_from_the_moment_the_tool_answers(run_dir):
    """The model writes `drawImage(img)` against the path it was given; a file that is not there
    yet is a broken image, and drawing one throws every frame — a black game until the render
    lands. A sprite stand-in is matted, a scene's is opaque, because the game draws one ON its
    background and the other AS it."""
    from PIL import Image
    request_media(RUN, run_dir, "goblin", "a snarling goblin")
    request_media(RUN, run_dir, "floor", "a stone floor", kind="scene")
    goblin = Image.open(assets.asset_path(RUN, "goblin", "webp"))
    floor = Image.open(assets.asset_path(RUN, "floor", "webp"))
    assert goblin.format == "WEBP" and goblin.convert("RGBA").getpixel((0, 0))[3] == 0
    assert floor.format == "WEBP" and floor.convert("RGBA").getpixel((0, 0))[3] == 255
    assert not assets.asset_path(RUN, "hut", "glb").exists()


def test_a_placeholder_is_not_a_landed_render(run_dir):
    """Everything that used to read 'file exists' as 'art arrived' — the repeat-ask answer, the
    top-up, the batch finalize — reads the manifest flag instead, or a stand-in would count as
    art and the real render would never be paid for."""
    request_media(RUN, run_dir, "goblin", "a snarling goblin")
    again = request_media(RUN, run_dir, "goblin", "a snarling goblin")
    assert again["status"] == "rendering"
    assert assets.landed(RUN, read_manifest(run_dir)[0]) is False

    assets.start_from_manifest(RUN, run_dir)
    assert len(_image_jobs()) == 2, "the top-up re-renders past a placeholder"


def test_a_landed_render_replaces_the_placeholder_and_the_flag(run_dir, monkeypatch, tmp_path):
    from PIL import Image
    from maestro.codegen.asset_chain import OPERATIONS
    request_media(RUN, run_dir, "floor", "a stone floor", kind="scene")
    src = tmp_path / "render.png"
    Image.new("RGBA", (64, 64), (0, 255, 0, 255)).save(src)
    monkeypatch.setattr("maestro.codegen.asset_chain._record_defect", lambda md, d: None)
    OPERATIONS["save_flat"]({"run_id": RUN, "asset_id": "floor", "kind": "scene"},
                            {"images": [{"file": str(src),
                                         "safety": {"scores": {"NSFW": 0.0, "SFW": 1.0}}}]})
    entry = read_manifest(run_dir)[0]
    assert "placeholder" not in entry and assets.landed(RUN, entry)
    assert Image.open(assets.asset_path(RUN, "floor", "webp")).size == (64, 64)
    assert request_media(RUN, run_dir, "floor", "a stone floor")["status"] == "ready"


def test_a_confirmed_replace_keeps_the_landed_art_until_the_new_render(run_dir, monkeypatch):
    request_media(RUN, run_dir, "goblin", "a goblin")
    assets.set_landed(run_dir, "goblin")
    assets.asset_path(RUN, "goblin", "webp").write_bytes(b"REAL")
    request_media(RUN, run_dir, "goblin", "a bigger goblin")
    request_media(RUN, run_dir, "goblin", "a bigger goblin")
    assert assets.asset_path(RUN, "goblin", "webp").read_bytes() == b"REAL"
    assert "placeholder" not in read_manifest(run_dir)[0]


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

    assert (out["path"], out["status"]) == ("assets/goblin.webp", "rendering")
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
    (run_dir / "game" / "assets" / "goblin.webp").write_bytes(b"RIFFwebp")
    out = request_media(RUN, run_dir, "goblin", "a snarling goblin")
    assert out == {"ok": True, "path": "assets/goblin.webp", "status": "ready"}
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
    monkeypatch.setattr(assets, "build_image_payload", lambda *a, **kw: None)
    out = request_media(RUN, run_dir, "goblin", "something refused")
    assert out["ok"] is False and "draw this one with code" in out["error"]
    assert read_manifest(run_dir) == []


def test_an_exhausted_budget_tells_the_model_to_draw_it_instead(run_dir):
    """The compute budget is the only cap on how much art a build may ask for — there is no call
    limit, because a refused enqueue already says so in words the model can act on."""
    with store._db() as conn:
        conn.execute("UPDATE games SET seconds_used = 10000 WHERE id = ?", (RUN,))
    out = request_media(RUN, run_dir, "goblin", "a goblin")
    assert out["ok"] is False and "draw this one with code" in out["error"]
    assert _image_jobs() == []
    assert read_manifest(run_dir) == []


def test_the_tool_reaches_the_queue_and_defaults_to_a_sprite(run_dir):
    tools = build_tools(RunState(RUN))
    out = tools["generate_media"](id="goblin", prompt="a snarling goblin")
    assert (out["ok"], out["path"], out["status"]) == (True, "assets/goblin.webp", "rendering")
    assert _image_jobs()[0]["metadata"]["kind"] == "sprite"


def test_the_tool_reports_a_missing_argument_rather_than_guessing(run_dir):
    tools = build_tools(RunState(RUN))
    assert tools["generate_media"](prompt="a goblin")["ok"] is False
    assert tools["generate_media"](id="goblin")["ok"] is False
    assert _image_jobs() == []


def _jobs(queue):
    with store._db() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE queue = ? ORDER BY id", (queue,)).fetchall()
    return [store._job_dict(r) for r in rows]


KNIGHT_ANIMS = [{"name": "walk", "action": "walks in place, legs alternating"},
                {"name": "attack", "action": "swings the sword once"}]


def test_an_anim_answers_with_a_png_path_and_chains_the_video_worker(run_dir):
    out = request_media(RUN, run_dir, "knight", "a knight", kind="anim", anims=KNIGHT_ANIMS, facings=4)
    assert out["path"] == "assets/knight.png"
    md = _image_jobs()[0]["metadata"]
    assert md["kind"] == "anim"
    assert md["then"] == {"enqueue": "anim_from_image", "finalize": "assets"}


def test_an_anim_placeholder_writes_both_the_png_and_the_manifest(run_dir):
    request_media(RUN, run_dir, "knight", "a knight", kind="anim", anims=KNIGHT_ANIMS, facings=4)
    png = run_dir / "game" / "assets" / "knight.png"
    manifest = run_dir / "game" / "assets" / "knight.json"
    assert png.exists() and manifest.exists()
    entry = read_manifest(run_dir)[0]
    assert entry["kind"] == "anim"
    assert entry["anims"] == KNIGHT_ANIMS and entry["facings"] == 4
    # the stand-in manifest already answers to the names the game will ask for
    assert set(json.loads(manifest.read_text())["anims"]) == {"walk", "attack"}


def test_an_anim_request_carries_its_spec_to_the_video_leg(run_dir):
    request_media(RUN, run_dir, "car", "a red racing car seen from above", kind="anim",
                  anims=[{"name": "drive", "action": "the wheels spin"}], facings=1)
    md = _image_jobs()[0]["metadata"]
    assert md["anims"] == [{"name": "drive", "action": "the wheels spin"}]
    assert md["facings"] == 1


@pytest.mark.parametrize("anims, facings, word", [
    (None, 4, "anims"),
    ([], 4, "anims"),
    ([{"name": "walk"}], 4, "action"),
    ([{"name": "walk", "action": "walks"}, {"name": "walk", "action": "walks"}], 4, "distinct"),
    ([{"name": "walk", "action": "walks"}], 2, "facings"),
    ([{"name": "walk", "action": "walks"}], None, "facings"),
])
def test_an_anim_without_a_full_spec_is_refused_with_the_reason(run_dir, anims, facings, word):
    out = request_media(RUN, run_dir, "knight", "a knight", kind="anim", anims=anims,
                        facings=facings)
    assert out["ok"] is False and word in out["error"]
    assert _image_jobs() == []


def test_a_top_up_resumes_an_anim_from_its_source_render_onto_the_video_queue(run_dir):
    """A mesh needs its source render to skip back to TRELLIS; an anim needs the same to skip
    back to the video worker instead of re-paying for the still."""
    request_media(RUN, run_dir, "knight", "a knight", kind="anim", anims=KNIGHT_ANIMS, facings=4)
    src = assets.asset_path(RUN, "knight", "src.png")
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x89PNG-the-still-we-already-paid-for")

    assets.start_from_manifest(RUN, run_dir)

    video_jobs = _jobs("video")
    assert len(video_jobs) == 1, "the top-up went to the video worker, not back to ComfyUI"
    assert video_jobs[0]["payload"]["kind"] == "anim_sheet"
    assert set(video_jobs[0]["payload"]["anims"]) == {"walk", "attack"}
    assert video_jobs[0]["payload"]["turn"] is not None
    assert video_jobs[0]["metadata"]["then"]["operations"] == ["save_anim"]
    assert len(_jobs("image")) == 1, "no second still render was paid for"


def test_an_anim_with_no_source_render_still_starts_at_the_image(run_dir):
    request_media(RUN, run_dir, "knight", "a knight", kind="anim", anims=KNIGHT_ANIMS, facings=4)

    assets.start_from_manifest(RUN, run_dir)

    assert len(_jobs("image")) == 2 and _jobs("video") == []


def test_kind_video_is_still_an_unknown_kind(run_dir):
    """anim is the new kind; video (the queue it lands on, not something a game asks for) must
    stay refused."""
    out = request_media(RUN, run_dir, "knight", "a knight", kind="video")
    assert out["ok"] is False and "video" in out["error"]


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
