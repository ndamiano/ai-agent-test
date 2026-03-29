"""
Tests for agent store and agent tools.
"""

import pytest
import json
import tempfile
import shutil

from agents.agent_store import AgentStore
from tools.tool_manager import tool_manager
from tools.agent_tools import register_agent_tools


@pytest.fixture(scope="function")
def temp_agent_store():
    """Create a temporary agent store for testing"""
    AgentStore.reset()
    temp_dir = tempfile.mkdtemp()
    store = AgentStore(store_dir=temp_dir)
    yield store
    shutil.rmtree(temp_dir)


@pytest.fixture(scope="function", autouse=True)
def ensure_tools_registered():
    """Ensure agent tools are registered before tests"""
    if 'list_agents' not in {tool['name'] for tool in tool_manager.getTools()}:
        register_agent_tools()
    yield


def create_test_agent(agent_id: str = "test-agent") -> dict:
    """Helper to create a valid test agent spec"""
    return {
        "id": agent_id,
        "name": "Test Agent",
        "description": "A test agent for unit testing purposes",
        "system_prompt": "You are a test agent designed to validate the agent store system.",
        "tools": ["write_to_file", "read_file"],
    }


class TestAgentStore:
    """Test agent store operations"""

    def test_save_and_get(self, temp_agent_store):
        """Should save and retrieve an agent"""
        agent = create_test_agent()
        temp_agent_store.save(agent)
        retrieved = temp_agent_store.get("test-agent")
        assert retrieved["id"] == "test-agent"
        assert retrieved["name"] == "Test Agent"

    def test_get_nonexistent_raises(self, temp_agent_store):
        """Should raise KeyError for non-existent agent"""
        with pytest.raises(KeyError):
            temp_agent_store.get("nonexistent")

    def test_list(self, temp_agent_store):
        """Should list all agents"""
        temp_agent_store.save(create_test_agent("agent-1"))
        temp_agent_store.save(create_test_agent("agent-2"))
        agents = temp_agent_store.list()
        assert len(agents) == 2
        assert {a["id"] for a in agents} == {"agent-1", "agent-2"}

    def test_list_empty(self, temp_agent_store):
        """Should return empty list when no agents exist"""
        assert temp_agent_store.list() == []

    def test_exists(self, temp_agent_store):
        """Should correctly report existence"""
        assert not temp_agent_store.exists("test-agent")
        temp_agent_store.save(create_test_agent())
        assert temp_agent_store.exists("test-agent")

    def test_save_missing_fields_raises(self, temp_agent_store):
        """Should raise ValueError for missing required fields"""
        with pytest.raises(ValueError, match="required field"):
            temp_agent_store.save({"id": "test", "name": "Test"})


class TestAgentTools:
    """Test agent tools (list_agents, request_agent)"""

    def test_list_agents_tool(self):
        """list_agents tool should return agent roster"""
        result_json = tool_manager.useTool("list_agents")
        result = json.loads(result_json)
        assert "agents" in result
        assert "count" in result

    def test_request_agent_tool(self):
        """request_agent tool should log demand"""
        result_json = tool_manager.useTool(
            "request_agent",
            capability="deep cultural analysis of fandom topics",
            reason="Generic worker lacks nuanced fan community context",
        )
        result = json.loads(result_json)
        assert result["success"] is True
        assert "total_requests" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])