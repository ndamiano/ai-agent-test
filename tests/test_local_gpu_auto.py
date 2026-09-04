"""`scripts/local_gpu.py auto`'s decision function — pure, no process spawning."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_gpu", ROOT / "scripts" / "local_gpu.py")
local_gpu = importlib.util.module_from_spec(spec)
sys.modules["local_gpu"] = local_gpu
spec.loader.exec_module(local_gpu)


def test_nothing_pending_holds_nothing():
    assert local_gpu.choose_action(None, {"llm": 0, "image": 0, "mesh": 0}, {}, 3) is None


def test_starts_on_priority_order_when_nothing_held():
    pending = {"llm": 0, "image": 2, "mesh": 1}
    assert local_gpu.choose_action(None, pending, {}, 3) == "image"


def test_llm_wins_priority_over_image_and_mesh():
    pending = {"llm": 1, "image": 2, "mesh": 1}
    assert local_gpu.choose_action(None, pending, {}, 3) == "llm"


def test_held_queue_keeps_the_card_while_it_has_work():
    pending = {"llm": 1, "image": 5, "mesh": 0}
    idle_ticks = {"llm": 0, "image": 0, "mesh": 0}
    assert local_gpu.choose_action("llm", pending, idle_ticks, 3) == "llm"


def test_a_starving_queue_takes_the_card_from_a_busy_one():
    """A build's turns keep the llm queue non-empty; the sheets behind it must not age into the
    reaper's cut-off."""
    pending = {"llm": 1, "image": 0, "mesh": 0, "video": 5}
    waits = {"llm": 5.0, "image": 0.0, "mesh": 0.0, "video": local_gpu.STARVE_SECONDS + 1}
    assert local_gpu.choose_action("llm", pending, {"llm": 0}, 3, waits) == "video"
    # under the cut-off the held queue keeps the card
    waits["video"] = local_gpu.STARVE_SECONDS - 1
    assert local_gpu.choose_action("llm", pending, {"llm": 0}, 3, waits) == "llm"


def test_the_longest_starving_queue_wins_and_the_held_one_never_counts():
    pending = {"llm": 1, "image": 2, "mesh": 0, "video": 1}
    waits = {"llm": 9999.0, "image": local_gpu.STARVE_SECONDS + 5,
             "mesh": 0.0, "video": local_gpu.STARVE_SECONDS + 50}
    assert local_gpu.choose_action("llm", pending, {"llm": 0}, 3, waits) == "video"


def test_held_queue_keeps_card_through_a_momentary_empty():
    # a completion just fired; a continuation may land before the idle-tick limit
    pending = {"llm": 0, "image": 5, "mesh": 0}
    idle_ticks = {"llm": 1, "image": 0, "mesh": 0}
    assert local_gpu.choose_action("llm", pending, idle_ticks, 3) == "llm"


def test_held_queue_yields_once_drained_and_another_has_work():
    pending = {"llm": 0, "image": 5, "mesh": 0}
    idle_ticks = {"llm": 3, "image": 0, "mesh": 0}
    assert local_gpu.choose_action("llm", pending, idle_ticks, 3) == "image"


def test_held_queue_drained_and_nothing_else_pending_releases_the_card():
    # idle-exit timing is handled by the caller (auto loop); the decision function just
    # reports "nothing wants the card"
    pending = {"llm": 0, "image": 0, "mesh": 0}
    idle_ticks = {"llm": 3, "image": 0, "mesh": 0}
    assert local_gpu.choose_action("llm", pending, idle_ticks, 3) is None


def test_llm_leg_serves_vision(monkeypatch):
    monkeypatch.setattr(local_gpu, "_model_id", lambda: "m")
    monkeypatch.setattr(local_gpu, "_ninfer_artifact", lambda: Path("/m.ninfer"))
    monkeypatch.setattr(local_gpu, "_settings", lambda: {"llm": {"n_ctx": 4096}})
    assert "--vision" in local_gpu._leg("llm")["argv"]


def test_mesh_leg_ready_waits_for_warm(monkeypatch):
    import io
    import json

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    answers = iter([{"status": "ok", "warm": False}, {"status": "ok", "warm": True}])
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=2.0: _Resp(json.dumps(next(answers)).encode()),
    )
    assert local_gpu._ready("mesh", "http://x/health") is False
    assert local_gpu._ready("mesh", "http://x/health") is True


def test_ninfer_artifact_is_the_nvfp4_build_of_the_model_id(monkeypatch, tmp_path):
    """The groupwise-int sibling is the wrong artifact, not a fallback: worldgen's judges read
    renders through the vision tower, and nvfp4/fp8 is the one they were measured on."""
    monkeypatch.setattr(local_gpu, "_model_id", lambda: "qwen3.8_27b")
    monkeypatch.setattr(local_gpu, "NINFER_MODELS", tmp_path)
    (tmp_path / "qwen3_8_27b.ninfer").write_bytes(b"x")
    with __import__("pytest").raises(SystemExit):
        local_gpu._ninfer_artifact()
    (tmp_path / "qwen3_8_27b_nvfp4.ninfer").write_bytes(b"x")
    assert local_gpu._ninfer_artifact() == tmp_path / "qwen3_8_27b_nvfp4.ninfer"
