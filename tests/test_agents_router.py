"""Tests for api.routers.agents endpoints and _agent_to_response helper."""

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routers.agents as agents_module
from api.routers.agents import router, _agent_to_response
from api.models.responses import AgentResponse


# --- _agent_to_response unit tests ---

class TestAgentToResponse:
    def test_full_agent_dict(self):
        agent = {
            "id": "alpha",
            "name": "Alpha Agent",
            "description": "First agent",
            "tools": ["read_file", "write_to_file"],
        }
        resp = _agent_to_response(agent)
        assert isinstance(resp, AgentResponse)
        assert resp.id == "alpha"
        assert resp.name == "Alpha Agent"
        assert resp.description == "First agent"
        assert resp.tools == ["read_file", "write_to_file"]

    def test_extra_keys_ignored(self):
        agent = {
            "id": "beta",
            "name": "Beta",
            "description": "Has extra keys",
            "tools": [],
            "system_prompt": "ignored",
            "created_at": "2025-01-01T00:00:00Z",
        }
        resp = _agent_to_response(agent)
        assert resp.id == "beta"
        assert resp.tools == []


# --- Endpoint test helpers ---

SAMPLE_AGENTS = [
    {
        "id": "agent-1",
        "name": "Agent One",
        "description": "First test agent",
        "tools": ["read_file"],
    },
    {
        "id": "agent-2",
        "name": "Agent Two",
        "description": "Second test agent",
        "tools": ["write_to_file", "read_file"],
    },
]


def _make_client(store_mock):
    """Return a TestClient with _agent_store patched to the given mock."""
    app = FastAPI()
    app.include_router(router, prefix="/api/agents")
    return TestClient(app, raise_server_exceptions=False), \
           patch.object(agents_module, "_agent_store", store_mock)


# --- Endpoint tests ---

class TestListAgents:
    def test_returns_all_agents(self):
        store = MagicMock()
        store.list.return_value = list(SAMPLE_AGENTS)
        client, ctx = _make_client(store)
        with ctx:
            response = client.get("/api/agents/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == "agent-1"
        assert data[1]["id"] == "agent-2"

    def test_response_has_correct_fields(self):
        store = MagicMock()
        store.list.return_value = list(SAMPLE_AGENTS)
        client, ctx = _make_client(store)
        with ctx:
            response = client.get("/api/agents/")
        expected_keys = {"id", "name", "description", "tools"}
        for agent_data in response.json():
            assert set(agent_data.keys()) == expected_keys

    def test_empty_list(self):
        store = MagicMock()
        store.list.return_value = []
        client, ctx = _make_client(store)
        with ctx:
            response = client.get("/api/agents/")
        assert response.status_code == 200
        assert response.json() == []


class TestGetAgent:
    def test_returns_single_agent(self):
        store = MagicMock()
        store.get.return_value = SAMPLE_AGENTS[0]
        client, ctx = _make_client(store)
        with ctx:
            response = client.get("/api/agents/agent-1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "agent-1"
        assert data["name"] == "Agent One"
        assert data["description"] == "First test agent"
        assert data["tools"] == ["read_file"]

    def test_response_has_correct_fields(self):
        store = MagicMock()
        store.get.return_value = SAMPLE_AGENTS[1]
        client, ctx = _make_client(store)
        with ctx:
            response = client.get("/api/agents/agent-2")
        assert set(response.json().keys()) == {"id", "name", "description", "tools"}

    def test_not_found_returns_404(self):
        store = MagicMock()
        store.get.side_effect = KeyError("nonexistent")
        client, ctx = _make_client(store)
        with ctx:
            response = client.get("/api/agents/nonexistent")
        assert response.status_code == 404
        assert "nonexistent" in response.json()["detail"]


class TestResponseConsistency:
    def test_same_agent_matches(self):
        store = MagicMock()
        store.list.return_value = [SAMPLE_AGENTS[0]]
        store.get.return_value = SAMPLE_AGENTS[0]
        client, ctx = _make_client(store)
        with ctx:
            list_resp = client.get("/api/agents/")
            get_resp = client.get("/api/agents/agent-1")
        assert list_resp.status_code == 200
        assert get_resp.status_code == 200
        assert list_resp.json()[0] == get_resp.json()
