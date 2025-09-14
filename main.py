from agents.main_agent import MainAgent
from tools.project_manager import project_manager
from tools.project import Project

def main():
    """Interactive chat loop with the main agent"""
    print("=== AI Creative Agent System ===")
    print("Type 'quit' or 'exit' to end the conversation")
    print("Type 'clear' to clear conversation history")
    print("Type 'history' to view message history\n")
    
    # Set up default project
    default_project = Project("interactive-session")
    project_manager.set_current_project(default_project)
    
    # Create main agent
    agent = MainAgent()
    
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