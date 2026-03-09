#!/usr/bin/env python3
"""
Demo script showing the agent store functionality
"""

import json
from agents.agent_store import AgentStore
from agents.main_agent import MainAgent
from agents.agent_spawner import AgentSpawner


def demo_agent_store():
    """Demonstrate agent store functionality"""
    print("=== Agent Store Demo ===\n")
    
    # Initialize agent store
    agent_store = AgentStore()
    
    # List existing agents
    print("1. Listing existing agents:")
    agents = agent_store.list()
    for agent in agents:
        print(f"   - {agent['id']}: {agent['name']}")
        print(f"     Description: {agent['description']}")
        print(f"     Tools: {', '.join(agent['tools'])}")
        print()
    
    # Create a custom agent
    print("2. Creating a custom agent:")
    custom_agent = {
        "id": "demo-agent",
        "name": "Demo Agent",
        "description": "A demonstration agent for testing purposes",
        "system_prompt": "You are a helpful demo agent that provides clear, concise responses and demonstrates agent store functionality.",
        "tools": ["spawn_agent", "list_tools"]
    }
    
    agent_store.save(custom_agent)
    print(f"   Created agent: {custom_agent['name']}")
    print(f"   ID: {custom_agent['id']}")
    print(f"   Tools: {', '.join(custom_agent['tools'])}")
    print()
    
    # Load the agent
    print("3. Loading the custom agent:")
    loaded_agent = agent_store.get("demo-agent")
    print(f"   Loaded: {loaded_agent['name']}")
    print(f"   System prompt: {loaded_agent['system_prompt'][:50]}...")
    print()
    
    # Update the agent
    print("4. Updating the agent:")
    loaded_agent["description"] = "An updated demo agent with enhanced capabilities"
    agent_store.save(loaded_agent)
    updated_agent = agent_store.get("demo-agent")
    print(f"   Updated description: {updated_agent['description']}")
    print(f"   Updated at: {updated_agent['updated_at']}")
    print()
    
    # Demo using agent with MainAgent
    print("5. Using agent with MainAgent:")
    try:
        agent = MainAgent(agent_id="demo-agent")
        print(f"   MainAgent initialized with agent: {updated_agent['name']}")
        print(f"   System context: {agent.system_context[:50]}...")
        print(f"   Available tools: {len(agent._tools_schema)} tools")
    except Exception as e:
        print(f"   Error: {e}")
    print()
    
    # Demo using agent with AgentSpawner
    print("6. Using agent with AgentSpawner:")
    try:
        spawner = AgentSpawner()
        result = spawner.spawn_agent(
            task="What can you tell me about agent stores?",
            agent_id="demo-agent"
        )
        print(f"   Spawner result: {result[:100]}...")
    except Exception as e:
        print(f"   Error: {e}")
    print()
    
    # Clean up
    print("7. Cleaning up:")
    agent_store.delete("demo-agent")
    print("   Deleted demo-agent")
    
    # Verify deletion
    if not agent_store.exists("demo-agent"):
        print("   ✓ Agent successfully deleted")
    else:
        print("   ✗ Agent still exists")
    
    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    demo_agent_store()