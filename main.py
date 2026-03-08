"""Minimal example of the refactored agentic loop"""

from agents.main_agent import MainAgent
from tools.project_manager import project_manager
from tools.project import Project
from tools.tool_manager import tool_manager


def create_example_tools():
    """Create some example tools to demonstrate the system"""
    
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


def main():
    """Interactive chat loop with the main agent"""
    print("=== Generic Agentic Loop ===")
    print("Type 'quit' or 'exit' to end the conversation")
    print("Type 'clear' to clear conversation history")
    print("Type 'history' to view message history")
    print("Type 'tools' to list available tools\n")
    
    # Create a project
    project = Project("example-project")
    project_manager.set_current_project(project)
    
    # Create example tools
    create_example_tools()
    
    # Create main agent
    agent = MainAgent()
    
    print("Available tools:")
    print(tool_manager.list_tools())
    print()
    
    while True:
        try:
            user_input = input("You: ").strip()
            
            if user_input.lower() in ['quit', 'exit']:
                print("\nGoodbye!")
                break
            elif user_input.lower() == 'clear':
                agent.clear_history()
                print("Conversation history cleared.")
                continue
            elif user_input.lower() == 'history':
                history = agent.get_message_history()
                if not history:
                    print("No conversation history.")
                else:
                    print("\n=== Message History ===")
                    for i, msg in enumerate(history, 1):
                        role = msg['role'].title()
                        content = msg['content'][:100] + "..." if len(msg['content']) > 100 else msg['content']
                        print(f"{i}. {role}: {content}")
                    print("=====================\n")
                continue
            elif user_input.lower() == 'tools':
                print("\nAvailable tools:")
                print(tool_manager.list_tools())
                print()
                continue
            elif not user_input:
                continue
            
            # Get response from agent
            print("\nAgent:", end=" ")
            response = agent.chat(user_input)
            print(response)
            print()
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            print("Please try again.\n")


if __name__ == "__main__":
    main()