"""Test script to verify logging functionality"""

from tools.tool_manager import tool_manager
from tools.logging_utils import tool_logger


def test_tool_logging():
    """Test that tool calls are properly logged"""
    
    print("=== Testing Tool Logging ===")
    
    # Create a simple test tool
    def test_add(a: int, b: int) -> str:
        """Add two numbers"""
        result = a + b
        return f"Test result: {result}"
    
    # Register the test tool
    tool_manager.register_tool(
        name="test_add",
        description="Test addition tool",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "First number"},
                "b": {"type": "integer", "description": "Second number"}
            },
            "required": ["a", "b"]
        },
        fn=test_add
    )
    
    print("1. Testing successful tool call...")
    try:
        result = tool_manager.useTool("test_add", a=5, b=3)
        print(f"   Result: {result}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print("\n2. Testing failed tool call...")
    try:
        result = tool_manager.useTool("nonexistent_tool", a=5, b=3)
        print(f"   Result: {result}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print("\n3. Testing tool with invalid arguments...")
    try:
        result = tool_manager.useTool("test_add", a=5)  # Missing required parameter 'b'
        print(f"   Result: {result}")
    except Exception as e:
        print(f"   Error: {e}")
    
    print(f"\n4. Session log file: {tool_logger.get_session_log_path()}")
    
    # Check if log file was created
    log_path = tool_logger.get_session_log_path()
    if os.path.exists(log_path):
        print("✅ Log file created successfully")
        print("\nLog file contents:")
        with open(log_path, 'r') as f:
            for line in f:
                log_entry = json.loads(line.strip())
                print(f"  {log_entry['type']}: {log_entry.get('tool_name', log_entry.get('decision', log_entry.get('error_type', 'Unknown')))}")
    else:
        print("❌ Log file was not created")


if __name__ == "__main__":
    import os
    import json
    test_tool_logging()