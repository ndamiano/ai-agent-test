"""Complete system test with tool registration and logging"""

from tools.tool_manager import tool_manager
from tools.logging_utils import log_tool_call, log_agent_decision, log_error
from agents.main_agent import MainAgent


def create_example_tools():
    """Create the same example tools as in main.py"""
    
    def add_numbers(a: int, b: int) -> str:
        """Add two numbers together"""
        result = a + b
        return f"The sum of {a} and {b} is {result}"
    
    def multiply_numbers(a: int, b: int) -> str:
        """Multiply two numbers together"""
        result = a * b
        return f"The product of {a} and {b} is {result}"
    
    def get_current_time() -> str:
        """Get the current time"""
        import datetime
        return f"The current time is {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    # Register the tools
    tool_manager.register_tool(
        name="add_numbers",
        description="Add two numbers together",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "First number"},
                "b": {"type": "integer", "description": "Second number"}
            },
            "required": ["a", "b"]
        },
        fn=add_numbers
    )
    
    tool_manager.register_tool(
        name="multiply_numbers",
        description="Multiply two numbers together",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "First number"},
                "b": {"type": "integer", "description": "Second number"}
            },
            "required": ["a", "b"]
        },
        fn=multiply_numbers
    )
    
    tool_manager.register_tool(
        name="get_current_time",
        description="Get the current time",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        fn=get_current_time
    )


def test_complete_system():
    """Test the complete system with tools and logging"""
    
    print("=== Complete System Test ===")
    
    # Register tools
    print("1. Registering example tools...")
    create_example_tools()
    
    # Check tools
    tools = tool_manager.getTools()
    print(f"   Registered {len(tools)} tools:")
    for tool in tools:
        print(f"   - {tool['name']}: {tool['description']}")
    
    # Test 1: Direct tool calls
    print("\n2. Testing direct tool calls with logging")
    
    print("   a) Testing multiply_numbers(7, 8)")
    try:
        result = tool_manager.useTool("multiply_numbers", a=7, b=8)
        print(f"      Result: {result}")
    except Exception as e:
        print(f"      Error: {e}")
    
    print("   b) Testing add_numbers(15, 25)")
    try:
        result = tool_manager.useTool("add_numbers", a=15, b=25)
        print(f"      Result: {result}")
    except Exception as e:
        print(f"      Error: {e}")
    
    print("   c) Testing get_current_time()")
    try:
        result = tool_manager.useTool("get_current_time")
        print(f"      Result: {result}")
    except Exception as e:
        print(f"      Error: {e}")
    
    # Test 2: Agent with tools
    print("\n3. Testing MainAgent with tools")
    
    agent = MainAgent()
    
    print("   a) Testing agent with tool call: 'What's 5 times 6?'")
    try:
        response = agent.chat("What's 5 times 6?")
        print(f"      Agent response: {response}")
    except Exception as e:
        print(f"      Error: {e}")
    
    print("   b) Testing agent with non-tool request: 'Hello'")
    try:
        response = agent.chat("Hello")
        print(f"      Agent response: {response}")
    except Exception as e:
        print(f"      Error: {e}")
    
    # Test 3: Error scenarios
    print("\n4. Testing error scenarios")
    
    print("   a) Testing nonexistent tool")
    try:
        result = tool_manager.useTool("nonexistent_tool", a=5, b=3)
        print(f"      Result: {result}")
    except Exception as e:
        print(f"      Error: {e}")
    
    print("   b) Testing invalid arguments")
    try:
        result = tool_manager.useTool("add_numbers", a=5)  # Missing 'b'
        print(f"      Result: {result}")
    except Exception as e:
        print(f"      Error: {e}")
    
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
        
        # Categorize entries
        tool_calls = []
        agent_decisions = []
        errors = []
        
        for line in lines:
            try:
                import json
                log_entry = json.loads(line.strip())
                if log_entry['type'] == 'tool_call':
                    tool_calls.append(log_entry)
                elif log_entry['type'] == 'agent_decision':
                    agent_decisions.append(log_entry)
                elif log_entry['type'] == 'error':
                    errors.append(log_entry)
            except json.JSONDecodeError:
                continue
        
        print(f"Tool calls: {len(tool_calls)}")
        print(f"Agent decisions: {len(agent_decisions)}")
        print(f"Errors: {len(errors)}")
        
        # Show successful tool calls
        if tool_calls:
            print("\nSuccessful tool calls:")
            for i, entry in enumerate(tool_calls, 1):
                if entry['success']:
                    print(f"  {i}. {entry['tool_name']}")
                    print(f"     Parameters: {entry['parameters']}")
                    print(f"     Result: {entry['result']}")
        
        # Show agent decisions
        if agent_decisions:
            print("\nAgent decisions:")
            for i, entry in enumerate(agent_decisions, 1):
                print(f"  {i}. {entry['decision']}")
                print(f"     Input: {entry['user_input'][:50]}...")
        
        # Show errors
        if errors:
            print("\nErrors:")
            for i, entry in enumerate(errors, 1):
                print(f"  {i}. {entry['error_type']}: {entry['error_message']}")
    else:
        print("❌ Log file does not exist")
    
    print("\n=== Test completed ===")


if __name__ == "__main__":
    test_complete_system()