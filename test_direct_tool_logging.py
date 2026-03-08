"""Direct test of tool calling with logging"""

from tools.tool_manager import tool_manager
from tools.logging_utils import log_tool_call, log_agent_decision, log_error


def test_direct_tool_logging():
    """Test tool calling directly with logging"""
    
    print("=== Direct Tool Logging Test ===")
    
    # Test 1: Successful tool call
    print("\n1. Testing successful tool call")
    try:
        result = tool_manager.useTool("multiply_numbers", a=7, b=8)
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 2: Another successful tool call
    print("\n2. Testing another successful tool call")
    try:
        result = tool_manager.useTool("add_numbers", a=15, b=25)
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 3: Get current time
    print("\n3. Testing get_current_time tool")
    try:
        result = tool_manager.useTool("get_current_time")
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 4: Failed tool call
    print("\n4. Testing failed tool call")
    try:
        result = tool_manager.useTool("nonexistent_tool", a=5, b=3)
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 5: Invalid arguments
    print("\n5. Testing invalid arguments")
    try:
        result = tool_manager.useTool("add_numbers", a=5)  # Missing 'b'
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 6: Manual logging
    print("\n6. Testing manual logging")
    log_agent_decision(
        user_input="What's 10 times 10?",
        decision="Calling multiply_numbers tool",
        details={"tool_name": "multiply_numbers", "arguments": {"a": 10, "b": 10}}
    )
    
    log_tool_call(
        tool_name="multiply_numbers",
        parameters={"a": 10, "b": 10},
        result="The product of 10 and 10 is 100"
    )
    
    log_error(
        error_type="TestError",
        error_message="This is a test error",
        context={"test": "manual_logging"}
    )
    
    # Check log file
    from tools.logging_utils import tool_logger
    log_path = tool_logger.get_session_log_path()
    print(f"\n=== Log Analysis ===")
    print(f"Log file: {log_path}")
    
    import os
    if os.path.exists(log_path):
        print("✅ Log file exists")
        
        with open(log_path, 'r') as f:
            lines = f.readlines()
        
        print(f"Total log entries: {len(lines)}")
        
        for i, line in enumerate(lines, 1):
            try:
                import json
                log_entry = json.loads(line.strip())
                print(f"\n{i}. {log_entry['type'].upper()}:")
                if log_entry['type'] == 'tool_call':
                    print(f"   Tool: {log_entry['tool_name']}")
                    print(f"   Parameters: {log_entry['parameters']}")
                    print(f"   Success: {log_entry['success']}")
                    if log_entry['result']:
                        print(f"   Result: {log_entry['result']}")
                    if log_entry['error']:
                        print(f"   Error: {log_entry['error']}")
                elif log_entry['type'] == 'agent_decision':
                    print(f"   Decision: {log_entry['decision']}")
                    print(f"   User input: {log_entry['user_input']}")
                elif log_entry['type'] == 'error':
                    print(f"   Error type: {log_entry['error_type']}")
                    print(f"   Message: {log_entry['error_message']}")
                    print(f"   Context: {log_entry['context']}")
            except json.JSONDecodeError:
                print(f"{i}. Invalid JSON: {line.strip()}")
    else:
        print("❌ Log file does not exist")
    
    print("\n=== Test completed ===")


if __name__ == "__main__":
    test_direct_tool_logging()