"""Test script to verify agent logging with actual tool calls"""

from agents.main_agent import MainAgent
from tools.tool_manager import tool_manager


def test_agent_with_logging():
    """Test the MainAgent with our logging system"""
    
    print("=== Testing MainAgent with Logging ===")
    
    # Create the agent
    agent = MainAgent()
    
    # Test 1: Simple tool call
    print("\n1. Testing tool call: 'What's 7 times 8?'")
    response = agent.chat("What's 7 times 8?")
    print(f"Agent response: {response}")
    
    # Test 2: Another tool call
    print("\n2. Testing tool call: 'Add 15 and 25'")
    response = agent.chat("Add 15 and 25")
    print(f"Agent response: {response}")
    
    # Test 3: Get current time
    print("\n3. Testing tool call: 'What time is it?'")
    response = agent.chat("What time is it?")
    print(f"Agent response: {response}")
    
    # Test 4: Non-tool request
    print("\n4. Testing non-tool request: 'Hello, how are you?'")
    response = agent.chat("Hello, how are you?")
    print(f"Agent response: {response}")
    
    # Test 5: Invalid tool request
    print("\n5. Testing invalid tool request: 'Call nonexistent_tool with parameters'")
    response = agent.chat("Call nonexistent_tool with parameters")
    print(f"Agent response: {response}")
    
    print("\n=== Test completed ===")


if __name__ == "__main__":
    test_agent_with_logging()