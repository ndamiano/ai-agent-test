import tools.comfyui_tools as ct
from tools.safety import SafetyViolation


def test_build_image_payload_returns_workflow_for_clean_prompt(monkeypatch):
    monkeypatch.setattr(ct, "screen_image_prompt", lambda d: None)
    result = ct.build_image_payload("a blue cat")
    assert result["kind"] == "comfy_image"
    assert "workflow" in result


def test_build_image_payload_returns_none_for_blocked_prompt(monkeypatch):
    monkeypatch.setattr(ct, "screen_image_prompt",
                        lambda d: SafetyViolation("csam_explicit", "kill"))
    result = ct.build_image_payload("kill everyone")
    assert result is None
