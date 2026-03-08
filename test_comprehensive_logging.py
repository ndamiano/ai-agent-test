"""Comprehensive test to verify all logging functionality"""

from agents.main_agent import MainAgent
from tools.tool_manager import tool_manager
from tools.logging_utils import tool_logger
import os
import json


def test_comprehensive_logging():
    """Test all logging functionality comprehensively"""
    
    print("=== Comprehensive Logging Test ===")
    
    # Create a new agent with native tools enabled
    agent = MainAgent(use_native_tools=True)
    
    print(f"Session log file: {tool_logger.get_session_log_path()}")
    
    # Test 1: Successful tool call with native tools
    print("\n1. Testing native tool call: 'What's 5 times 6?'")
    try:
        response = agent.chat("What's 5 times 6?")
        print(f"Agent response: {response}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 2: Multiple tool calls
    print("\n2. Testing multiple tool calls")
    try:
        response = agent.chat("Add 10 and 20, then multiply 3 and 4")
        print(f"Agent response: {response}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 3: Non-tool request
    print("\n3. Testing non-tool request")
    try:
        response = agent.chat("Tell me a joke")
        print(f"Agent response: {response}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 4: Tool call with text-based fallback
    print("\n4. Testing text-based tool call")
    agent_text = MainAgent(use_native_tools=False)
    try:
        response = agent_text.chat("What's 7 times 8?")
        print(f"Agent response: {response}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Check log file
    log_path = tool_logger.get_session_log_path()
    print(f"\n=== Log Analysis ===")
    print(f"Log file: {log_path}")
    
    if os.path.exists(log_path):
        print("✅ Log file exists")
        
        # Read and analyze log entries
        with open(log_path, 'r') as f:
            log_entries = []
            for line in f:
                try:
                    log_entry = json.loads(line.strip())
                    log_entries.append(log_entry)
                except json.JSONDecodeError:
                    continue
        
        print(f"Total log entries: {len(log_entries)}")
        
        # Categorize log entries
        tool_calls = [entry for entry in log_entries if entry['type'] == 'tool_call']
        agent_decisions = [entry for entry in log_entries if entry['type'] == 'agent_decision']
        errors = [entry for entry in log_entries if entry['type'] == 'error']
        
        print(f"Tool calls logged: {len(tool_calls)}")
        print(f"Agent decisions logged: {len(agent_decisions)}")
        print(f"Errors logged: {len(errors)}")
        
        # Show tool call details
        if tool_calls:
            print("\nTool call details:")
            for i, entry in enumerate(tool_calls, 1):
                print(f"  {i}. Tool: {entry['tool_name']}")
                print(f"     Parameters: {entry['parameters']}")
                print(f"     Success: {entry['success']}")
                if entry['result']:
                    print(f"     Result: {entry['result'][:100]}...")
                if entry['error']:
                    print(f"     Error: {entry['error']}")
        
        # Show agent decisions
        if agent_decisions:
            print("\nAgent decisions:")
            for i, entry in enumerate(agent_decisions, 1):
                print(f"  {i}. Decision: {entry['decision']}")
                print(f"     User input: {entry['user_input'][:50]}...")
        
        # Show errors
        if errors:
            print("\nErrors:")
            for i, entry in enumerate(errors, 1):
                print(f"  {i}. {entry['error_type']}: {entry['error_message']}")
    else:
        print("❌ Log file does not exist")
    
    print("\n=== Test completed ===")


if __name__ == "__main__":
    test_comprehensive_logging()