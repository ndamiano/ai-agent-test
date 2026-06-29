"""Engine-aware LLM VRAM management: the bracket frees the LLM via whichever model-management
API the configured endpoint speaks (LM Studio native REST vs llama.cpp router), auto-detected,
and no-ops when the endpoint speaks neither (e.g. a single-model llama-server)."""
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import tools.comfyui_tools as ct


def _http_404(url):
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


@pytest.fixture(autouse=True)
def _reset_flavor():
    ct.reset_llm_flavor_cache()
    yield
    ct.reset_llm_flavor_cache()


# --- detection --------------------------------------------------------------

def test_detects_lmstudio_when_api_v0_answers(monkeypatch):
    monkeypatch.setattr(ct, "_http_get", lambda url: {"data": []})  # /api/v0/models 200
    assert ct._detect_llm_flavor() == "lmstudio"


def test_detects_llamacpp_router_by_status_field(monkeypatch):
    def get(url):
        if "/api/v0/models" in url:
            _http_404(url)
        return {"data": [{"id": "m", "status": {"value": "loaded"}}]}
    monkeypatch.setattr(ct, "_http_get", get)
    assert ct._detect_llm_flavor() == "llamacpp"


def test_detects_none_when_neither_answers(monkeypatch):
    # plain single-model llama-server: /api/v0/models 404, bare /models 404 too
    monkeypatch.setattr(ct, "_http_get", _http_404)
    assert ct._detect_llm_flavor() == "none"


def test_none_flavor_makes_unload_a_noop(monkeypatch):
    monkeypatch.setattr(ct, "_http_get", _http_404)
    posted = []
    monkeypatch.setattr(ct, "_http_post", lambda url, data: posted.append(url) or {})
    assert ct._llm_get_loaded_model() is None
    assert ct._llm_unload("anything") is False
    assert posted == []  # no eviction attempted against an unmanageable server


# --- llama.cpp router calls -------------------------------------------------

def test_llamacpp_get_loaded_returns_loaded_id(monkeypatch):
    monkeypatch.setattr(ct, "_http_get", lambda url: {"data": [
        {"id": "cold", "status": {"value": "unloaded"}},
        {"id": "hot", "status": {"value": "loaded"}},
    ]})
    assert ct._llamacpp_get_loaded_model() == "hot"


def test_llamacpp_unload_posts_model_and_polls(monkeypatch):
    posts = []
    monkeypatch.setattr(ct, "_http_post", lambda url, data: posts.append((url, data)) or {})
    # poll immediately reports the model unloaded so the wait loop exits
    monkeypatch.setattr(ct, "_http_get", lambda url: {"data": [{"id": "hot", "status": {"value": "unloaded"}}]})
    assert ct._llamacpp_unload("hot") is True
    url, data = posts[0]
    assert url.endswith("/models/unload")
    assert data == {"model": "hot"}


def test_llamacpp_load_posts_model(monkeypatch):
    posts = []
    monkeypatch.setattr(ct, "_http_post", lambda url, data: posts.append((url, data)) or {})
    assert ct._llamacpp_load("hot") is True
    url, data = posts[0]
    assert url.endswith("/models/load")
    assert data == {"model": "hot"}


# --- dispatch routes to the detected backend --------------------------------

def test_dispatch_routes_to_llamacpp(monkeypatch):
    def get(url):
        if "/api/v0/models" in url:
            _http_404(url)
        return {"data": [{"id": "hot", "status": {"value": "loaded"}}]}
    monkeypatch.setattr(ct, "_http_get", get)
    assert ct._llm_get_loaded_model() == "hot"
